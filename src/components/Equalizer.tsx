import { DropdownItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { FC, useState } from "react";
import { EqSettings, saveEq, setSetting } from "../api";
import { EQ_BANDS, EQ_PRESETS, EQ_RANGE, formatBand } from "../eq";
import { player } from "../player";
import { pluginState, useStore } from "../store";
import { ACCENT } from "./common";
import type { SectionFrame } from "./Settings";

let saveTimer: number | undefined;

/**
 * Applies immediately to the audio graph; persists to the backend once the user stops moving, for the
 * output it was set on (if the output changes meanwhile, it's still that one's).
 */
function updateEq(eq: EqSettings) {
  const state = pluginState.get();
  if (state) pluginState.set({ ...state, eq });
  player.setEq(eq);
  const output = state?.eq_per_output ? (state.output?.id ?? null) : null;
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => saveEq(eq, output), 800);
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

/** `Section`: how its heading is drawn where it's shown (the media center's own; Quick Access's by default) */
export const Equalizer: FC<{ Section?: SectionFrame }> = ({ Section = PanelSection }) => {
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
    <Section title="Equalizer">
      {state.output && (
        <PanelSectionRow>
          <ToggleField
            label="For each output"
            description={
              state.eq_per_output ? `Now: ${state.output.name}` : "Speakers, headphones and Bluetooth keep their own"
            }
            checked={state.eq_per_output}
            onChange={async (on) => {
              pluginState.set({ ...state, eq_per_output: on });
              await setSetting("eq_per_output", on);
            }}
          />
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ToggleField label="Enabled" checked={eq.enabled} onChange={(enabled) => updateEq({ ...eq, enabled })} />
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          // under its label, full width in the narrow panel (its value never cut short); beside it, as every other
          // choice is, where there's room (the media center)
          layout={Section === PanelSection ? "below" : "inline"}
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
    </Section>
  );
};
