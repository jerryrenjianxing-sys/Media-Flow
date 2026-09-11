# Home badge pixel rules v2

v2 retains all v1 templates and thresholds. It adds one sanitized `6` glyph
from the manually labeled A16 training crop captured on 2026-09-11. No full
screenshot, account, device identifier or text outside the badge is included.

The separately captured B6 crop comes from another device and is held out
from template generation. `real-v2-crops.json` keeps the two original lossless
badge crops as base64 PNGs for reproducible regression; training A16 is not
independent acceptance. B6 was observed to fail before the correction and is
a regression holdout, not a blind test proving general accuracy.

The runtime still requires a confirmed home/message region and rejects
ambiguous glyphs, conflicting UI counts and nonnumeric shapes. Score 0.64,
class separation 0.075 and aspect thresholds are unchanged. There is no
hash lookup, network request or device-specific coordinate in this rule.

This adds evidence for one real `6` font. It does not demonstrate 90% or 95%
accuracy across unseen fonts, all digits, red dots, compression or occlusion.
Missing UI/page evidence remains a failed check, never “no messages”.
