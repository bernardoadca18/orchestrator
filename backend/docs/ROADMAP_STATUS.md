# NeoVids roadmap status

The current architecture roadmap, Stages 1–18, is implemented and frozen through deterministic
Automated Production QA / Generalization Hardening.

The final artifact chain is:

```text
script -> voice -> alignment -> visual plan -> production timeline -> AudioMixPlan -> render -> QA release gate
```

Remaining operational validation debt is explicitly **not another roadmap stage**:

- repair/reindex a usable real managed gameplay dataset;
- run a complete real gameplay production;
- execute standard and optionally deep QA;
- perform the human checklist for semantic footage choice and editorial quality.

Automated Stage 18 results must not be represented as proof of editorial excellence. There is no
Stage 19 in this roadmap and no automatic publishing/repair implementation.

Program Audio V1 is implemented inside Stages 17–18: deterministic controlled music resolution,
ducking, lossless intermediate mix, separate candidate program mastering, final stereo AAC, and
technical QA. Its gain/master settings still require human A/B review before they can be described
as human-frozen.
