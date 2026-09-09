# Home badge pixel rules v1

The JSON holds 68 normalized 20×28 grayscale masks: generic raster glyphs
0–9/+ in Arial regular/bold at 20/24/32px (66), plus two sanitized real training
glyphs (1 and 3) from one early device-A screenshot showing 13. Fonts are not
loaded by the runtime. The real original-three regression is not a template.

Runtime candidates must be white ink horizontally enclosed by a previously
located red component. Column segmentation preserves all glyphs; red islands
inside 0/6/8/9 are contained by the enclosing badge. Character shape is scaled,
aspect checked, then compared by soft intersection-over-union. Minimum score
0.64 and a minimum 0.075 separation between different characters are required.
Any rejected character rejects the entire quantity. Only positive displayed
counts and an optional trailing + become quantities; no screenshot hashes,
network services or device access participate in recognition.

Independent real evidence currently covers displayed 1, 2 and 4 only (three
representatives from devices other than training/development devices).
Development regression covers original 3; real training covers 13. Generic
masks are not a real validation claim for 0, 5, 6, 7, 8, 9 or +. More fonts,
small compressed ink and occlusion may be rejected. In particular 540px-wide
JPEG derivatives of the original3 are intentionally unreadable; visible badge
presence is retained and optional already-enabled vision can still read it.
