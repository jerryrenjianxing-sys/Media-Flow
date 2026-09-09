# Badge regression pixels

`real-three.png` is the lossless 36×36 red badge crop from the reported original
720×1600 screenshot. Original bounds are `[527,1431,563,1467]` (right/bottom
exclusive). It contains only the badge. Tests paste these unchanged pixels into
a sanitized canvas and run both `analyze_badge` and the formal inspector.

`synthetic-*.png` are explicitly synthetic typography fixtures (Arial Bold,
20px) for segmentation, enclosed red counters, and `99+` quantity semantics.
They are not real-device acceptance evidence. JPEG/downscale test variants also
do not enter real-sample accuracy figures.

Full original screenshots, paired XML, manual labels, the device/time split and
linked offline replay report remain outside the source tree in the local
`MediaFlow-Task-Prep/dev40-audit` evidence directory. The original three is a
development regression and excluded from independent acceptance.
