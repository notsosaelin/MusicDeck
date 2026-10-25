/*
 * mtp-helper: phones and players plugged in as MTP devices ("File transfer" on Android), on Windows,
 * where nothing mounts them as folders the way GVfs does on Linux. It talks to them through Windows
 * Portable Devices (WPD, part of Windows), and answers in JSON lines (musicdeck/mtp.py runs it):
 *
 *   mtp-helper devices
 *       {"id": "\\\\?\\usb#vid_...", "name": "Pixel 8", "maker": "Google"}            one per device
 *   mtp-helper list <device> [top folder]...
 *       {"type": "storage", "id", "name", "free", "total"}                             each storage
 *       {"type": "folder"|"file", "storage", "path", "id", "size", "mtime"}            what's under the
 *       top folders named (Music, Download...) on each storage, or under all of it when none is there
 *   mtp-helper get <device>        then lines of <object id> TAB <local path> on stdin
 *       {"id", "ok": true, "size"} or {"id", "error"}                                  one per line
 *   mtp-helper put <device> <storage name> <folder path> <local file> [name]
 *       {"id"} of the new file; folders on the way are made
 *   mtp-helper delete <device> <object id>
 *       {"ok": true}
 *
 * Errors are {"error": "..."} and a non-zero exit. Public domain-ish: MusicDeck's own (GPL-3.0-or-later).
 */
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <objbase.h>
/* the WPD headers' GUIDs and property keys defined here (MinGW's import library has only a few of them) */
#include <initguid.h>
#include <propkeydef.h>
#include <propvarutil.h>
#include <portabledeviceapi.h>
#include <portabledevice.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

static IPortableDevice *device;
static IPortableDeviceContent *content;
static IPortableDeviceProperties *props;

/* --- output -------------------------------------------------------------------------------------- */

static void put_string(const wchar_t *w)
{
	putchar('"');
	if (w) {
		int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
		char *s = malloc(n > 0 ? n : 1);
		WideCharToMultiByte(CP_UTF8, 0, w, -1, s, n, NULL, NULL);
		for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
			if (*p == '"' || *p == '\\')
				printf("\\%c", *p);
			else if (*p < 0x20)
				printf("\\u%04x", *p);
			else
				putchar(*p);
		}
		free(s);
	}
	putchar('"');
}

static void fail(const char *what, HRESULT hr)
{
	printf("{\"error\": \"%s (0x%08lx)\"}\n", what, (unsigned long)hr);
	fflush(stdout);
	exit(1);
}

/* --- devices ------------------------------------------------------------------------------------- */

static IPortableDeviceManager *manager(void)
{
	IPortableDeviceManager *m = NULL;
	HRESULT hr = CoCreateInstance(&CLSID_PortableDeviceManager, NULL, CLSCTX_INPROC_SERVER,
	                              &IID_IPortableDeviceManager, (void **)&m);
	if (FAILED(hr))
		fail("Windows Portable Devices isn't available", hr);
	return m;
}

typedef HRESULT (*name_fn)(IPortableDeviceManager *, LPCWSTR, WCHAR *, DWORD *);

static wchar_t *ask(IPortableDeviceManager *m, LPCWSTR id, name_fn fn)
{
	DWORD n = 0;
	if (FAILED(fn(m, id, NULL, &n)) || !n)
		return NULL;
	wchar_t *s = calloc(n + 1, sizeof(wchar_t));
	if (FAILED(fn(m, id, s, &n))) {
		free(s);
		return NULL;
	}
	return s;
}

static HRESULT friendly(IPortableDeviceManager *m, LPCWSTR id, WCHAR *s, DWORD *n)
{ return IPortableDeviceManager_GetDeviceFriendlyName(m, id, s, n); }
static HRESULT maker(IPortableDeviceManager *m, LPCWSTR id, WCHAR *s, DWORD *n)
{ return IPortableDeviceManager_GetDeviceManufacturer(m, id, s, n); }
static HRESULT description(IPortableDeviceManager *m, LPCWSTR id, WCHAR *s, DWORD *n)
{ return IPortableDeviceManager_GetDeviceDescription(m, id, s, n); }

static HRESULT try_open(const wchar_t *id);

/* what the device itself says it is (its own name, model, maker, serial number), as opposed to the
   driver's ("MTP USB Device", "(Standard MTP Device)"); "" for what it doesn't say */
static void device_facts(const wchar_t *id, wchar_t **name, wchar_t **model, wchar_t **made, wchar_t **serial)
{
	*name = *model = *made = *serial = NULL;
	if (FAILED(try_open(id)))
		return;
	IPortableDeviceKeyCollection *k = NULL;
	IPortableDeviceValues *v = NULL;
	CoCreateInstance(&CLSID_PortableDeviceKeyCollection, NULL, CLSCTX_INPROC_SERVER,
	                 &IID_IPortableDeviceKeyCollection, (void **)&k);
	IPortableDeviceKeyCollection_Add(k, &WPD_DEVICE_FRIENDLY_NAME);
	IPortableDeviceKeyCollection_Add(k, &WPD_DEVICE_MODEL);
	IPortableDeviceKeyCollection_Add(k, &WPD_DEVICE_MANUFACTURER);
	IPortableDeviceKeyCollection_Add(k, &WPD_DEVICE_SERIAL_NUMBER);
	if (SUCCEEDED(IPortableDeviceProperties_GetValues(props, WPD_DEVICE_OBJECT_ID, k, &v)) && v) {
		IPortableDeviceValues_GetStringValue(v, &WPD_DEVICE_FRIENDLY_NAME, name);
		IPortableDeviceValues_GetStringValue(v, &WPD_DEVICE_MODEL, model);
		IPortableDeviceValues_GetStringValue(v, &WPD_DEVICE_MANUFACTURER, made);
		IPortableDeviceValues_GetStringValue(v, &WPD_DEVICE_SERIAL_NUMBER, serial);
		IPortableDeviceValues_Release(v);
	}
	IPortableDeviceKeyCollection_Release(k);
	IPortableDeviceProperties_Release(props);
	IPortableDeviceContent_Release(content);
	IPortableDevice_Close(device);
	IPortableDevice_Release(device);
	props = NULL;
	content = NULL;
	device = NULL;
}

static int cmd_devices(void)
{
	IPortableDeviceManager *m = manager();
	DWORD count = 0;
	IPortableDeviceManager_RefreshDeviceList(m);
	if (FAILED(IPortableDeviceManager_GetDevices(m, NULL, &count)) || !count)
		return 0;
	LPWSTR *ids = calloc(count, sizeof(LPWSTR));
	IPortableDeviceManager_GetDevices(m, ids, &count);
	for (DWORD i = 0; i < count; i++) {
		wchar_t *name = ask(m, ids[i], friendly);
		if (!name || !*name)
			name = ask(m, ids[i], description);
		wchar_t *made = ask(m, ids[i], maker);
		printf("{\"id\": ");
		put_string(ids[i]);
		printf(", \"name\": ");
		put_string(name);
		printf(", \"maker\": ");
		put_string(made);
		/* drives Windows Portable Devices wraps (wpdbusenum) are drive letters, and not asked */
		if (!wcsstr(ids[i], L"wpdbusenum")) {
			wchar_t *own, *model, *own_maker, *serial;
			device_facts(ids[i], &own, &model, &own_maker, &serial);
			printf(", \"own_name\": ");
			put_string(own);
			printf(", \"model\": ");
			put_string(model);
			printf(", \"own_maker\": ");
			put_string(own_maker);
			printf(", \"serial\": ");
			put_string(serial);
			CoTaskMemFree(own);
			CoTaskMemFree(model);
			CoTaskMemFree(own_maker);
			CoTaskMemFree(serial);
		}
		printf("}\n");
		free(name);
		free(made);
		CoTaskMemFree(ids[i]);
	}
	free(ids);
	return 0;
}

static void open_device(const wchar_t *id)
{
	HRESULT hr = try_open(id);
	if (FAILED(hr))
		fail("The device wouldn't open (is it unlocked, and set to File transfer?)", hr);
}

static HRESULT try_open(const wchar_t *id)
{
	IPortableDeviceValues *client = NULL;
	HRESULT hr = CoCreateInstance(&CLSID_PortableDeviceValues, NULL, CLSCTX_INPROC_SERVER,
	                              &IID_IPortableDeviceValues, (void **)&client);
	if (FAILED(hr))
		fail("Windows Portable Devices isn't available", hr);
	IPortableDeviceValues_SetStringValue(client, &WPD_CLIENT_NAME, L"MusicDeck");
	IPortableDeviceValues_SetUnsignedIntegerValue(client, &WPD_CLIENT_MAJOR_VERSION, 1);
	IPortableDeviceValues_SetUnsignedIntegerValue(client, &WPD_CLIENT_MINOR_VERSION, 0);
	IPortableDeviceValues_SetUnsignedIntegerValue(client, &WPD_CLIENT_REVISION, 0);
	IPortableDeviceValues_SetUnsignedIntegerValue(client, &WPD_CLIENT_SECURITY_QUALITY_OF_SERVICE,
	                                              SECURITY_IMPERSONATION);
	hr = CoCreateInstance(&CLSID_PortableDeviceFTM, NULL, CLSCTX_INPROC_SERVER, &IID_IPortableDevice,
	                      (void **)&device);
	if (FAILED(hr))
		fail("Windows Portable Devices isn't available", hr);
	hr = IPortableDevice_Open(device, id, client);
	if (hr == E_ACCESSDENIED) {  /* some devices won't open for writing: read only, then */
		IPortableDeviceValues_SetUnsignedIntegerValue(client, &WPD_CLIENT_DESIRED_ACCESS, GENERIC_READ);
		hr = IPortableDevice_Open(device, id, client);
	}
	IPortableDeviceValues_Release(client);
	if (SUCCEEDED(hr))
		hr = IPortableDevice_Content(device, &content);
	if (SUCCEEDED(hr))
		hr = IPortableDeviceContent_Properties(content, &props);
	if (FAILED(hr)) {
		if (content)
			IPortableDeviceContent_Release(content);
		IPortableDevice_Release(device);
		device = NULL;
		content = NULL;
	}
	return hr;
}

/* --- objects ------------------------------------------------------------------------------------- */

typedef struct {
	wchar_t *name;
	GUID type;          /* WPD_CONTENT_TYPE_FOLDER, ..._FUNCTIONAL_OBJECT, anything else is a file */
	GUID category;      /* a functional object's: WPD_FUNCTIONAL_CATEGORY_STORAGE */
	ULONGLONG size, free, total;
	long long mtime;    /* Unix time, 0 when the device doesn't say */
} object_info;

static IPortableDeviceKeyCollection *keys;

static void make_keys(void)
{
	CoCreateInstance(&CLSID_PortableDeviceKeyCollection, NULL, CLSCTX_INPROC_SERVER,
	                 &IID_IPortableDeviceKeyCollection, (void **)&keys);
	IPortableDeviceKeyCollection_Add(keys, &WPD_OBJECT_NAME);
	IPortableDeviceKeyCollection_Add(keys, &WPD_OBJECT_ORIGINAL_FILE_NAME);
	IPortableDeviceKeyCollection_Add(keys, &WPD_OBJECT_CONTENT_TYPE);
	IPortableDeviceKeyCollection_Add(keys, &WPD_FUNCTIONAL_OBJECT_CATEGORY);
	IPortableDeviceKeyCollection_Add(keys, &WPD_OBJECT_SIZE);
	IPortableDeviceKeyCollection_Add(keys, &WPD_OBJECT_DATE_MODIFIED);
	IPortableDeviceKeyCollection_Add(keys, &WPD_STORAGE_FREE_SPACE_IN_BYTES);
	IPortableDeviceKeyCollection_Add(keys, &WPD_STORAGE_CAPACITY);
}

static long long unix_time(const PROPVARIANT *v)
{
	SYSTEMTIME st;
	FILETIME ft;
	if (v->vt != VT_DATE || !VariantTimeToSystemTime(v->date, &st) || !SystemTimeToFileTime(&st, &ft))
		return 0;
	ULONGLONG t = ((ULONGLONG)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
	return (long long)(t / 10000000ULL) - 11644473600LL;
}

static int info(LPCWSTR id, object_info *o)
{
	IPortableDeviceValues *v = NULL;
	memset(o, 0, sizeof(*o));
	if (FAILED(IPortableDeviceProperties_GetValues(props, id, keys, &v)) || !v)
		return 0;
	LPWSTR s = NULL;
	/* a file's own name (with its extension) is its original file name; WPD_OBJECT_NAME may lack it */
	if (SUCCEEDED(IPortableDeviceValues_GetStringValue(v, &WPD_OBJECT_ORIGINAL_FILE_NAME, &s)) && s && *s)
		o->name = s;
	else if (s)
		CoTaskMemFree(s);
	if (!o->name && SUCCEEDED(IPortableDeviceValues_GetStringValue(v, &WPD_OBJECT_NAME, &s)))
		o->name = s;
	IPortableDeviceValues_GetGuidValue(v, &WPD_OBJECT_CONTENT_TYPE, &o->type);
	IPortableDeviceValues_GetGuidValue(v, &WPD_FUNCTIONAL_OBJECT_CATEGORY, &o->category);
	IPortableDeviceValues_GetUnsignedLargeIntegerValue(v, &WPD_OBJECT_SIZE, &o->size);
	IPortableDeviceValues_GetUnsignedLargeIntegerValue(v, &WPD_STORAGE_FREE_SPACE_IN_BYTES, &o->free);
	IPortableDeviceValues_GetUnsignedLargeIntegerValue(v, &WPD_STORAGE_CAPACITY, &o->total);
	PROPVARIANT date;
	PropVariantInit(&date);
	if (SUCCEEDED(IPortableDeviceValues_GetValue(v, &WPD_OBJECT_DATE_MODIFIED, &date)))
		o->mtime = unix_time(&date);
	PropVariantClear(&date);
	IPortableDeviceValues_Release(v);
	return 1;
}

/* calls fn(child id, its info, ctx) for each child of `parent`; stops when fn returns 0 */
typedef int (*child_fn)(LPCWSTR id, object_info *o, void *ctx);

static void children(LPCWSTR parent, child_fn fn, void *ctx)
{
	IEnumPortableDeviceObjectIDs *e = NULL;
	if (FAILED(IPortableDeviceContent_EnumObjects(content, 0, parent, NULL, &e)) || !e)
		return;
	LPWSTR ids[64];
	ULONG got = 0;
	int more = 1;
	while (more && SUCCEEDED(IEnumPortableDeviceObjectIDs_Next(e, 64, ids, &got)) && got) {
		for (ULONG i = 0; i < got; i++) {
			object_info o;
			if (more && info(ids[i], &o)) {
				more = fn(ids[i], &o, ctx);
				CoTaskMemFree(o.name);
			}
			CoTaskMemFree(ids[i]);
		}
	}
	IEnumPortableDeviceObjectIDs_Release(e);
}

static int is_folder(const object_info *o)
{
	return IsEqualGUID(&o->type, &WPD_CONTENT_TYPE_FOLDER) || IsEqualGUID(&o->type, &WPD_CONTENT_TYPE_FUNCTIONAL_OBJECT);
}

static int is_storage(const object_info *o)
{
	return IsEqualGUID(&o->type, &WPD_CONTENT_TYPE_FUNCTIONAL_OBJECT) &&
	       IsEqualGUID(&o->category, &WPD_FUNCTIONAL_CATEGORY_STORAGE);
}

/* --- list ---------------------------------------------------------------------------------------- */

typedef struct {
	const wchar_t *storage;
	wchar_t path[2048];
	int tops;
	wchar_t **top;
	int found_top;
} walk_ctx;

static void print_entry(const char *type, const walk_ctx *w, LPCWSTR id, const object_info *o)
{
	printf("{\"type\": \"%s\", \"storage\": ", type);
	put_string(w->storage);
	printf(", \"path\": ");
	put_string(w->path);
	printf(", \"id\": ");
	put_string(id);
	printf(", \"size\": %llu, \"mtime\": %lld}\n", (unsigned long long)o->size, o->mtime);
}

static int walk(LPCWSTR id, object_info *o, void *ctx)
{
	walk_ctx *w = ctx;
	size_t at = wcslen(w->path);
	if (!o->name || at + wcslen(o->name) + 2 >= 2048)
		return 1;
	if (at)
		wcscat(w->path, L"/");
	wcscat(w->path, o->name);
	if (is_folder(o)) {
		print_entry("folder", w, id, o);
		children(id, walk, w);
	} else {
		print_entry("file", w, id, o);
	}
	w->path[at] = 0;
	return 1;
}

static int has_top(LPCWSTR id, object_info *o, void *ctx)
{
	walk_ctx *w = ctx;
	for (int i = 0; i < w->tops; i++)
		if (o->name && is_folder(o) && !_wcsicmp(o->name, w->top[i]))
			w->found_top = 1;
	return !w->found_top;
}

static int walk_tops(LPCWSTR id, object_info *o, void *ctx)
{
	walk_ctx *w = ctx;
	for (int i = 0; i < w->tops; i++)
		if (o->name && is_folder(o) && !_wcsicmp(o->name, w->top[i]))
			return walk(id, o, ctx);
	return 1;
}

static int each_storage(LPCWSTR id, object_info *o, void *ctx)
{
	walk_ctx *base = ctx;
	if (!is_storage(o))
		return 1;
	printf("{\"type\": \"storage\", \"id\": ");
	put_string(id);
	printf(", \"name\": ");
	put_string(o->name);
	printf(", \"free\": %llu, \"total\": %llu}\n", (unsigned long long)o->free, (unsigned long long)o->total);
	walk_ctx w = {o->name, L"", base->tops, base->top, 0};
	children(id, has_top, &w);
	children(id, w.found_top ? walk_tops : walk, &w);
	fflush(stdout);
	return 1;
}

static int cmd_list(const wchar_t *dev, int tops, wchar_t **top)
{
	open_device(dev);
	make_keys();
	walk_ctx w = {NULL, L"", tops, top, 0};
	children(WPD_DEVICE_OBJECT_ID, each_storage, &w);
	return 0;
}

/* --- get ------------------------------------------------------------------------------------------ */

static HRESULT fetch(LPCWSTR id, const wchar_t *dest, ULONGLONG *size)
{
	IPortableDeviceResources *res = NULL;
	IStream *stream = NULL;
	DWORD optimal = 0;
	HRESULT hr = IPortableDeviceContent_Transfer(content, &res);
	if (FAILED(hr))
		return hr;
	hr = IPortableDeviceResources_GetStream(res, id, &WPD_RESOURCE_DEFAULT, STGM_READ, &optimal, &stream);
	IPortableDeviceResources_Release(res);
	if (FAILED(hr))
		return hr;
	FILE *f = _wfopen(dest, L"wb");
	if (!f) {
		IStream_Release(stream);
		return HRESULT_FROM_WIN32(ERROR_CANNOT_MAKE);
	}
	DWORD chunk = optimal > 4096 ? optimal : 262144;
	char *buf = malloc(chunk);
	ULONG got = 0;
	*size = 0;
	while (SUCCEEDED(hr = IStream_Read(stream, buf, chunk, &got)) && got) {
		if (fwrite(buf, 1, got, f) != got) {
			hr = HRESULT_FROM_WIN32(ERROR_WRITE_FAULT);
			break;
		}
		*size += got;
	}
	if (hr == S_FALSE)
		hr = S_OK;
	free(buf);
	fclose(f);
	IStream_Release(stream);
	if (FAILED(hr))
		_wremove(dest);
	return hr;
}

static int cmd_get(const wchar_t *dev)
{
	open_device(dev);
	char line[8192];
	while (fgets(line, sizeof line, stdin)) {
		line[strcspn(line, "\r\n")] = 0;
		if (!memcmp(line, "\xEF\xBB\xBF", 3))  /* a byte order mark some shells put first */
			memmove(line, line + 3, strlen(line + 3) + 1);
		char *tab = strchr(line, '\t');
		if (!tab)
			continue;
		*tab = 0;
		wchar_t id[2048], dest[4096];
		MultiByteToWideChar(CP_UTF8, 0, line, -1, id, 2048);
		MultiByteToWideChar(CP_UTF8, 0, tab + 1, -1, dest, 4096);
		ULONGLONG size = 0;
		HRESULT hr = fetch(id, dest, &size);
		printf("{\"id\": ");
		put_string(id);
		if (SUCCEEDED(hr))
			printf(", \"ok\": true, \"size\": %llu}\n", (unsigned long long)size);
		else
			printf(", \"error\": \"couldn't read it (0x%08lx)\"}\n", (unsigned long)hr);
		fflush(stdout);
	}
	return 0;
}

/* --- put ----------------------------------------------------------------------------------------- */

typedef struct {
	const wchar_t *name;
	int folder;  /* only folders (and storages) match */
	wchar_t *found;
} find_ctx;

static int find_child(LPCWSTR id, object_info *o, void *ctx)
{
	find_ctx *f = ctx;
	if (o->name && !_wcsicmp(o->name, f->name) && (!f->folder || is_folder(o))) {
		f->found = _wcsdup(id);
		return 0;
	}
	return 1;
}

static wchar_t *child_named(LPCWSTR parent, const wchar_t *name, int folder)
{
	find_ctx f = {name, folder, NULL};
	children(parent, find_child, &f);
	return f.found;
}

static IPortableDeviceValues *values_for(LPCWSTR parent, const wchar_t *name)
{
	IPortableDeviceValues *v = NULL;
	CoCreateInstance(&CLSID_PortableDeviceValues, NULL, CLSCTX_INPROC_SERVER, &IID_IPortableDeviceValues, (void **)&v);
	IPortableDeviceValues_SetStringValue(v, &WPD_OBJECT_PARENT_ID, parent);
	IPortableDeviceValues_SetStringValue(v, &WPD_OBJECT_NAME, name);
	IPortableDeviceValues_SetStringValue(v, &WPD_OBJECT_ORIGINAL_FILE_NAME, name);
	return v;
}

static wchar_t *folder_in(LPCWSTR parent, const wchar_t *name)
{
	wchar_t *id = child_named(parent, name, 1);
	if (id)
		return id;
	IPortableDeviceValues *v = values_for(parent, name);
	IPortableDeviceValues_SetGuidValue(v, &WPD_OBJECT_CONTENT_TYPE, &WPD_CONTENT_TYPE_FOLDER);
	IPortableDeviceValues_SetGuidValue(v, &WPD_OBJECT_FORMAT, &WPD_OBJECT_FORMAT_PROPERTIES_ONLY);
	LPWSTR made = NULL;
	HRESULT hr = IPortableDeviceContent_CreateObjectWithPropertiesOnly(content, v, &made);
	IPortableDeviceValues_Release(v);
	if (FAILED(hr))
		fail("Couldn't make a folder on the device", hr);
	wchar_t *out = _wcsdup(made);
	CoTaskMemFree(made);
	return out;
}

static int cmd_put(const wchar_t *dev, const wchar_t *storage, const wchar_t *folder, const wchar_t *file,
                   const wchar_t *name)
{
	open_device(dev);
	make_keys();
	wchar_t *parent = child_named(WPD_DEVICE_OBJECT_ID, storage, 1);
	if (!parent)
		fail("That storage isn't on the device", E_INVALIDARG);
	wchar_t path[2048];
	wcsncpy(path, folder, 2047);
	path[2047] = 0;
	for (wchar_t *part = wcstok(path, L"/\\", NULL); part; part = wcstok(NULL, L"/\\", NULL)) {
		wchar_t *next = folder_in(parent, part);
		free(parent);
		parent = next;
	}
	if (!name) {
		name = wcsrchr(file, L'\\') ? wcsrchr(file, L'\\') + 1 : file;
		if (wcsrchr(name, L'/'))
			name = wcsrchr(name, L'/') + 1;
	}
	FILE *f = _wfopen(file, L"rb");
	if (!f)
		fail("Couldn't read the file to copy", E_INVALIDARG);
	_fseeki64(f, 0, SEEK_END);
	ULONGLONG size = _ftelli64(f);
	_fseeki64(f, 0, SEEK_SET);
	IPortableDeviceValues *v = values_for(parent, name);
	IPortableDeviceValues_SetUnsignedLargeIntegerValue(v, &WPD_OBJECT_SIZE, size);
	IStream *stream = NULL;
	DWORD optimal = 0;
	HRESULT hr = IPortableDeviceContent_CreateObjectWithPropertiesAndData(content, v, &stream, &optimal, NULL);
	IPortableDeviceValues_Release(v);
	if (FAILED(hr))
		fail("The device wouldn't take the file", hr);
	DWORD chunk = optimal > 4096 ? optimal : 262144;
	char *buf = malloc(chunk);
	size_t got;
	while ((got = fread(buf, 1, chunk, f)) > 0) {
		ULONG wrote = 0;
		if (FAILED(hr = IStream_Write(stream, buf, (ULONG)got, &wrote)) || wrote != got) {
			IStream_Revert(stream);
			fail("Copying to the device stopped", FAILED(hr) ? hr : E_FAIL);
		}
	}
	free(buf);
	fclose(f);
	if (FAILED(hr = IStream_Commit(stream, STGC_DEFAULT)))
		fail("The device didn't keep the file", hr);
	IPortableDeviceDataStream *ds = NULL;
	LPWSTR id = NULL;
	if (SUCCEEDED(IStream_QueryInterface(stream, &IID_IPortableDeviceDataStream, (void **)&ds))) {
		IPortableDeviceDataStream_GetObjectID(ds, &id);
		IPortableDeviceDataStream_Release(ds);
	}
	IStream_Release(stream);
	printf("{\"id\": ");
	put_string(id);
	printf("}\n");
	CoTaskMemFree(id);
	free(parent);
	return 0;
}

/* --- delete -------------------------------------------------------------------------------------- */

static int cmd_delete(const wchar_t *dev, const wchar_t *id)
{
	open_device(dev);
	IPortableDevicePropVariantCollection *ids = NULL;
	CoCreateInstance(&CLSID_PortableDevicePropVariantCollection, NULL, CLSCTX_INPROC_SERVER,
	                 &IID_IPortableDevicePropVariantCollection, (void **)&ids);
	PROPVARIANT pv;
	PropVariantInit(&pv);
	pv.vt = VT_LPWSTR;
	pv.pwszVal = (LPWSTR)id;
	IPortableDevicePropVariantCollection_Add(ids, &pv);
	HRESULT hr = IPortableDeviceContent_Delete(content, PORTABLE_DEVICE_DELETE_NO_RECURSION, ids, NULL);
	if (FAILED(hr))
		fail("The device wouldn't delete it", hr);
	printf("{\"ok\": true}\n");
	return 0;
}

int wmain(int argc, wchar_t **argv)
{
	SetConsoleOutputCP(CP_UTF8);
	if (FAILED(CoInitializeEx(NULL, COINIT_MULTITHREADED)))
		fail("COM didn't start", E_FAIL);
	int code = 2;
	if (argc >= 2 && !wcscmp(argv[1], L"devices"))
		code = cmd_devices();
	else if (argc >= 3 && !wcscmp(argv[1], L"list"))
		code = cmd_list(argv[2], argc - 3, argv + 3);
	else if (argc >= 3 && !wcscmp(argv[1], L"get"))
		code = cmd_get(argv[2]);
	else if (argc >= 6 && !wcscmp(argv[1], L"put"))
		code = cmd_put(argv[2], argv[3], argv[4], argv[5], argc >= 7 ? argv[6] : NULL);
	else if (argc >= 4 && !wcscmp(argv[1], L"delete"))
		code = cmd_delete(argv[2], argv[3]);
	else
		fprintf(stderr, "usage: mtp-helper devices | list <device> [top]... | get <device> | "
		                "put <device> <storage> <folder> <file> [name] | delete <device> <id>\n");
	fflush(stdout);
	return code;
}
