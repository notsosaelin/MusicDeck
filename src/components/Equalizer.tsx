import { DropdownItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { FC, useState } from "react";
import { EqSettings, setSetting } from "../api";
import { EQ_BANDS, EQ_PRESETS, EQ_RANGE, formatBand } from "../eq";
import { player } from "../player";
import { pluginState, useStore } from "../store";
import { ACCENT } from "./common";

let saveTimer: number | undefined;

/** Applies immediately to the audio graph; persists to the backend once the user stops moving. */
function updateEq(eq: EqSettings) {
  const state = pluginState.get();
  if (state) pluginState.set({ ...state, eq });
  player.setEq(eq);
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => setSetting("eq", eq), 800);
}

/** Compact bar view of the current curve, so presets can be compared without expanding every band. */
const EqCurve: FC<{ gains: number[]; enabled: boolean }> = ({ gains, enabled }) => {
  const height = 56;
  const mid = height / 2;
  return (
    <div style={{ opacity: enabled ? 1 : 0.4, padding: "6px 0 2px" }}>
      <div style={{ display: "flex", gap: 4, height, position: "relative" }}>
        <div
          style={{ position: "absolute", left: 0, right: 0, top: mid, borderTop: "1px solid rgba(255,255,255,0.15)" }}
        />
        {EQ_BANDS.map((hz, i) => {
          const g = gains[i] ?? 0;
          const h = (Math.abs(g) / EQ_RANGE) * mid;
          return (
            <div
              key={hz}
              style={{ flex: 1, position: "relative", background: "rgba(255,255,255,0.06)", borderRadius: 2 }}
            >
              <div
                style={{
                  position: "absolute",
                  left: 0,
                  right: 0,
                  top: g >= 0 ? mid - h : mid,
                  height: Math.max(h, 1),
                  background: ACCENT,
                  borderRadius: 2,
                }}
              />
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 4, fontSize: 11, opacity: 0.6, marginTop: 4 }}>
        {EQ_BANDS.map((hz) => (
          <div key={hz} style={{ flex: 1, textAlign: "center" }}>
            {formatBand(hz)}
          </div>
        ))}
      </div>
    </div>
  );
};

export const Equalizer: FC = () => {
  const state = useStore(pluginState);
  const [showBands, setShowBands] = useState(false);
  if (!state) return null;
  const eq = state.eq;

  const setBand = (i: number, gain: number) => {
    const gains = [...eq.gains];
    gains[i] = gain;
    updateEq({ ...eq, gains, preset: "Custom" });
  };

  return (
    <PanelSection title="Equalizer">
      <PanelSectionRow>
        <ToggleField label="Enabled" checked={eq.enabled} onChange={(enabled) => updateEq({ ...eq, enabled })} />
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          label="Preset"
          selectedOption={eq.preset}
          rgOptions={[
            ...Object.keys(EQ_PRESETS).map((name) => ({ data: name, label: name })),
            ...(eq.preset === "Custom" ? [{ data: "Custom", label: "Custom" }] : []),
          ]}
          onChange={(o) => EQ_PRESETS[o.data] && updateEq({ ...eq, preset: o.data, gains: [...EQ_PRESETS[o.data]] })}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <EqCurve gains={eq.gains} enabled={eq.enabled} />
      </PanelSectionRow>
      <PanelSectionRow>
        <SliderField
          label="Preamp"
          value={eq.preamp}
          min={-EQ_RANGE}
          max={EQ_RANGE}
          step={0.5}
          showValue
          valueSuffix=" dB"
          resetValue={0}
          disabled={!eq.enabled}
          onChange={(preamp) => updateEq({ ...eq, preamp })}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField label="Adjust bands" checked={showBands} disabled={!eq.enabled} onChange={setShowBands} />
      </PanelSectionRow>
      {showBands &&
        eq.enabled &&
        EQ_BANDS.map((hz, i) => (
          <PanelSectionRow key={hz}>
            <SliderField
              label={`${formatBand(hz)} Hz`}
              value={eq.gains[i] ?? 0}
              min={-EQ_RANGE}
              max={EQ_RANGE}
              step={0.5}
              showValue
              valueSuffix=" dB"
              resetValue={0}
              onChange={(v) => setBand(i, v)}
            />
          </PanelSectionRow>
        ))}
    </PanelSection>
  );
};
