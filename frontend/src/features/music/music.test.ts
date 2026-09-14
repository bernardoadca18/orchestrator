import assert from "node:assert/strict";
import test from "node:test";
import { MusicAsset } from "../../api/operator";
import { filterMusicAssets, selectedMusicAsset } from "./MusicLibraryPage";

const assets: MusicAsset[] = [{
  asset_id: "calm-01", sha256: "a".repeat(64), duration_seconds: 60, sample_rate: 48000,
  channels: 2, format: "wav", integrated_lufs: -22, true_peak_dbtp: -2,
  moods: ["calm"], styles: ["documentary"], energy: .2, loopable: true,
  source_owner: "NeoVids", license_type: "owned", monetization_allowed: true,
  attribution_required: false, attribution_text: null,
}, {
  asset_id: "action-01", sha256: "b".repeat(64), duration_seconds: 30, sample_rate: 48000,
  channels: 2, format: "flac", integrated_lufs: -18, true_peak_dbtp: -1,
  moods: ["tense"], styles: ["electronic"], energy: .9, loopable: false,
  source_owner: "Commission", license_type: "commissioned", monetization_allowed: true,
  attribution_required: true, attribution_text: "Music by X",
}];

test("music catalog filters and selection are deterministic", () => {
  assert.deepEqual(filterMusicAssets(assets, "", "calm", "", "low", "owned").map((item) => item.asset_id), ["calm-01"]);
  assert.equal(selectedMusicAsset(assets, "action-01")?.asset_id, "action-01");
  assert.equal(selectedMusicAsset(assets, "missing"), null);
});
