//! Catalogue and library reads over the librespot session, the way Spotifast does them: Spotify's
//! own client endpoints (spclient), not the Web API, so they don't count against the public app's
//! rate limit. Everything is returned shaped like the Web API's JSON, so MusicDeck maps it with the
//! same code either way.

use std::collections::HashMap;

use librespot_core::{session::Session, spotify_id::SpotifyId, FileId, SpotifyUri};
use librespot_metadata::image::Images;
use librespot_metadata::{Album, Artist, Lyrics, Metadata as _, Track};
use librespot_protocol::extended_metadata::{BatchedEntityRequest, EntityRequest, ExtensionQuery};
use librespot_protocol::extension_kind::ExtensionKind;
use librespot_protocol::playlist4_external::{ListAttributes, SelectedListContent};
use protobuf::{EnumOrUnknown, Message as _};
use serde_json::{json, Value};

type Res<T> = Result<T, String>;

const IMAGE_HOST: &str = "https://i.scdn.co/image/";
/// Rows per playlist or rootlist request, and entities per metadata batch.
const PAGE: usize = 500;
const PLAYLIST_MAX: usize = 5000;

fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}

fn base62(id: &str) -> Res<SpotifyId> {
    SpotifyId::from_base62(id).map_err(|_| format!("Bad Spotify id {id}"))
}

fn uri_id(uri: &SpotifyUri) -> String {
    uri.to_id().unwrap_or_default()
}

fn images(images: &Images) -> Value {
    let mut list: Vec<Value> = images
        .iter()
        .map(|i| json!({"url": format!("{IMAGE_HOST}{}", i.id), "width": i.width, "height": i.height}))
        .collect();
    list.sort_by_key(|i| i["width"].as_i64().unwrap_or(0));
    Value::Array(list)
}

fn artists(list: &librespot_metadata::artist::Artists) -> Value {
    list.iter().map(|a| json!({"id": uri_id(&a.id), "name": a.name})).collect()
}

fn album_json(a: &Album) -> Value {
    let year = a.date.year();
    json!({
        "id": uri_id(&a.id),
        "type": "album",
        "name": a.name,
        "artists": artists(&a.artists),
        "images": images(&a.covers),
        "release_date": if year > 0 { year.to_string() } else { String::new() },
    })
}

fn track_json(t: &Track) -> Value {
    json!({
        "id": uri_id(&t.id),
        "type": "track",
        "name": t.name,
        "duration_ms": t.duration,
        "artists": artists(&t.artists),
        "album": album_json(&t.album),
    })
}

/// Details of `uris` in batches, by URI; songs Spotify no longer has are left out.
async fn batch<T>(
    session: &Session,
    uris: &[String],
    kind: ExtensionKind,
    parse: impl Fn(&[u8]) -> Option<T>,
) -> Res<HashMap<String, T>> {
    let mut found = HashMap::new();
    for chunk in uris.chunks(PAGE) {
        let mut request = BatchedEntityRequest::new();
        for uri in chunk {
            request.entity_request.push(EntityRequest {
                entity_uri: uri.clone(),
                query: vec![ExtensionQuery { extension_kind: EnumOrUnknown::new(kind), ..Default::default() }],
                ..Default::default()
            });
        }
        let response = session.spclient().get_extended_metadata(request).await.map_err(err)?;
        for array in response.extended_metadata {
            if array.extension_kind.enum_value() != Ok(kind) {
                continue;
            }
            for data in array.extension_data {
                if !matches!(data.header.status_code, 0 | 200) {
                    continue;
                }
                if let Some(item) = data.extension_data.as_ref().and_then(|any| parse(&any.value)) {
                    found.insert(data.entity_uri, item);
                }
            }
        }
    }
    Ok(found)
}

async fn tracks_by_uri(session: &Session, uris: &[String]) -> Res<HashMap<String, Track>> {
    batch(session, uris, ExtensionKind::TRACK_V4, |bytes| {
        let message = librespot_protocol::metadata::Track::parse_from_bytes(bytes).ok()?;
        Track::try_from(&message).ok()
    })
    .await
}

async fn albums_by_uri(session: &Session, uris: &[String]) -> Res<HashMap<String, Album>> {
    batch(session, uris, ExtensionKind::ALBUM_V4, |bytes| {
        let message = librespot_protocol::metadata::Album::parse_from_bytes(bytes).ok()?;
        Album::try_from(&message).ok()
    })
    .await
}

/// Tracks in the order of `uris` (repeats kept, missing ones skipped).
async fn ordered_tracks(session: &Session, uris: &[String]) -> Res<Vec<Value>> {
    let mut unique: Vec<String> = Vec::new();
    for uri in uris {
        if uri.starts_with("spotify:track:") && !unique.contains(uri) {
            unique.push(uri.clone());
        }
    }
    let found = tracks_by_uri(session, &unique).await?;
    Ok(uris.iter().filter_map(|u| found.get(u)).map(track_json).collect())
}

pub async fn tracks(session: &Session, ids: &[String]) -> Res<Value> {
    let uris: Vec<String> = ids.iter().map(|id| format!("spotify:track:{id}")).collect();
    Ok(Value::Array(ordered_tracks(session, &uris).await?))
}

/// A picture reference as a list gives it: a web address, or an image URI on Spotify's host.
fn picture_url(reference: &str) -> Option<String> {
    if reference.starts_with("https://") || reference.starts_with("http://") {
        return Some(reference.to_string());
    }
    reference.strip_prefix("spotify:image:").map(|hex| format!("{IMAGE_HOST}{hex}"))
}

fn list_images(attributes: &ListAttributes) -> Value {
    let sized: Vec<Value> = attributes
        .picture_size
        .iter()
        .filter_map(|p| {
            let width = match p.target_name() {
                "small" => 60,
                "default" => 300,
                "large" => 640,
                _ => 0,
            };
            Some(json!({"url": picture_url(p.url())?, "width": width}))
        })
        .collect();
    if !sized.is_empty() {
        return Value::Array(sized);
    }
    if attributes.picture().is_empty() {
        return json!([]);
    }
    json!([{"url": format!("{IMAGE_HOST}{}", FileId::from_raw(attributes.picture())), "width": 300}])
}

async fn playlist_window(session: &Session, id: &SpotifyId, from: usize) -> Res<SelectedListContent> {
    let base62 = id.to_base62().map_err(err)?;
    let bytes = if from == 0 {
        session.spclient().get_playlist(id).await
    } else {
        let endpoint = format!("/playlist/v2/playlist/{base62}?from={from}&length={PAGE}");
        session.spclient().request(&http::Method::GET, &endpoint, None, None).await
    }
    .map_err(|e| format!("Couldn't read the playlist: {e}"))?;
    SelectedListContent::parse_from_bytes(&bytes).map_err(err)
}

/// The whole playlist: header and every track, read window by window.
pub async fn playlist(session: &Session, id: &str) -> Res<Value> {
    let spotify_id = base62(id)?;
    let first = playlist_window(session, &spotify_id, 0).await?;
    let length = usize::try_from(first.length()).unwrap_or_default().min(PLAYLIST_MAX);
    let mut uris: Vec<String> = Vec::new();
    let mut window = first.clone();
    loop {
        let before = uris.len();
        if let Some(contents) = window.contents.as_ref() {
            uris.extend(contents.items.iter().map(|i| i.uri().to_string()));
        }
        if uris.len() >= length || uris.len() == before {
            break;
        }
        window = playlist_window(session, &spotify_id, uris.len()).await?;
    }
    let attributes = first.attributes.get_or_default();
    let owner = first.owner_username().to_string();
    let tracks = ordered_tracks(session, &uris).await?;
    Ok(json!({
        "id": id,
        "name": attributes.name(),
        "description": attributes.description(),
        "images": list_images(attributes),
        "owner": {"id": owner, "display_name": display_name(session, &owner).await},
        "collaborative": attributes.collaborative(),
        "tracks": {"total": first.length()},
        "items": tracks,
    }))
}

/// The signed-in account's display name.
pub async fn profile(session: &Session) -> Res<Value> {
    Ok(json!({"display_name": display_name(session, &session.username()).await}))
}

/// A user's display name from the profile view Spotify's own clients read.
async fn display_name(session: &Session, user: &str) -> Value {
    if user.is_empty() || user == "spotify" {
        return json!(if user.is_empty() { "" } else { "Spotify" });
    }
    let read = tokio::time::timeout(std::time::Duration::from_secs(4), session.spclient().get_user_profile(user, Some(0), Some(0)));
    match read.await {
        Ok(Ok(bytes)) => serde_json::from_slice::<Value>(&bytes)
            .ok()
            .and_then(|v| v.get("name").and_then(Value::as_str).filter(|s| !s.is_empty()).map(Value::from))
            .unwrap_or_else(|| Value::from(user)),
        _ => Value::from(user),
    }
}

/// The account's playlist library (saved, followed and own playlists) in Spotify's order.
pub async fn rootlist(session: &Session) -> Res<Value> {
    let mut playlists = Vec::new();
    let mut owners: Vec<String> = Vec::new();
    let mut from = 0usize;
    loop {
        let bytes = session
            .spclient()
            .get_rootlist(from, Some(PAGE))
            .await
            .map_err(|e| format!("Couldn't read your playlists: {e}"))?;
        let response = SelectedListContent::parse_from_bytes(&bytes).map_err(err)?;
        let Some(contents) = response.contents.into_option() else { break };
        let count = contents.items.len();
        for (item, meta) in contents.items.iter().zip(contents.meta_items.iter()) {
            let Some(id) = item.uri().strip_prefix("spotify:playlist:") else { continue };
            if !matches!(meta.status_code(), 0 | 200) {
                continue;
            }
            let attributes = meta.attributes.get_or_default();
            let owner = meta.owner_username().to_string();
            if !owners.contains(&owner) {
                owners.push(owner.clone());
            }
            playlists.push(json!({
                "id": id,
                "name": attributes.name(),
                "images": list_images(attributes),
                "owner": {"id": owner},
                "collaborative": attributes.collaborative(),
                "tracks": {"total": meta.length()},
            }));
        }
        if !contents.truncated() || count == 0 {
            break;
        }
        from += count;
    }
    // owners' names, a few at a time and only briefly: ids show if a name doesn't arrive in time
    let mut names = HashMap::new();
    for chunk in owners.chunks(4).take(25) {
        let reads = chunk.iter().map(|o| display_name(session, o));
        for (owner, name) in chunk.iter().zip(futures_util::future::join_all(reads).await) {
            names.insert(owner.clone(), name);
        }
    }
    for p in &mut playlists {
        let owner = p["owner"]["id"].as_str().unwrap_or_default().to_string();
        p["owner"]["display_name"] = names.get(&owner).cloned().unwrap_or(Value::from(owner));
    }
    Ok(Value::Array(playlists))
}

pub async fn album(session: &Session, id: &str) -> Res<Value> {
    let uri = format!("spotify:album:{id}");
    let found = albums_by_uri(session, std::slice::from_ref(&uri)).await?;
    let a = found.get(&uri).ok_or("Not found on Spotify")?;
    let uris: Vec<String> = a.discs.iter().flat_map(|d| d.tracks.iter()).filter_map(|t| t.to_uri().ok()).collect();
    let mut value = album_json(a);
    value["tracks"] = Value::Array(ordered_tracks(session, &uris).await?);
    Ok(value)
}

pub async fn artist(session: &Session, id: &str) -> Res<Value> {
    let uri = SpotifyUri::Artist { id: base62(id)? };
    let a = Artist::get(session, &uri).await.map_err(|e| format!("Couldn't read the artist: {e}"))?;
    let country = session.country();
    let top = a
        .top_tracks
        .iter()
        .find(|t| t.country == country)
        .or_else(|| a.top_tracks.iter().find(|t| t.country == "ZZ"))
        .map(|t| t.tracks.iter().take(10).filter_map(|u| u.to_uri().ok()).collect::<Vec<_>>())
        .unwrap_or_default();
    let mut releases: Vec<String> = a.albums_current().chain(a.singles_current()).filter_map(|u| u.to_uri().ok()).collect();
    releases.truncate(100);
    let found = albums_by_uri(session, &releases).await?;
    let mut albums: Vec<&Album> = releases.iter().filter_map(|u| found.get(u)).collect();
    albums.sort_by_key(|a| std::cmp::Reverse(a.date.as_timestamp_ms()));
    // portraits only come with the full artist view; the newest cover stands in otherwise
    let art = if a.portraits.is_empty() { albums.first().map(|a| images(&a.covers)) } else { Some(images(&a.portraits)) };
    Ok(json!({
        "id": id,
        "name": a.name,
        "images": art.unwrap_or(json!([])),
        "top": ordered_tracks(session, &top).await?,
        "albums": albums.into_iter().map(album_json).collect::<Vec<_>>(),
    }))
}

/// Spotify's own lyrics (time-synced where it has them), in MusicDeck's shape; null when none.
pub async fn lyrics(session: &Session, id: &str) -> Res<Value> {
    let Ok(l) = Lyrics::get(session, &base62(id)?).await else { return Ok(Value::Null) };
    let synced = !matches!(l.lyrics.sync_type, librespot_metadata::lyrics::SyncType::Unsynced);
    let lines: Vec<Value> = l
        .lyrics
        .lines
        .iter()
        .map(|line| {
            let t = if synced { line.start_time_ms.parse::<f64>().ok().map(|ms| ms / 1000.0) } else { None };
            json!({"t": t, "text": line.words.replace('♪', "").trim()})
        })
        .collect();
    if !lines.iter().any(|l| !l["text"].as_str().unwrap_or_default().is_empty()) {
        return Ok(Value::Null);
    }
    let provider = if l.lyrics.provider_display_name.is_empty() {
        "Spotify".to_string()
    } else {
        format!("Spotify · {}", l.lyrics.provider_display_name)
    };
    Ok(json!({"synced": synced, "lines": lines, "source": provider}))
}
