# Country characters

Original hand-drawn stereotype figures used as meme characters in longform
segments. They are rendered by `src/autoshorts/longform/country_characters.py`
with Pillow; nothing in this folder is a copied or existing meme character.

- `canada`: knit toque, plaid flannel, paper coffee cup and a clipboard.
- `new_zealand`: hooped rugby-style jersey, dark shorts, knee bandage, friendly squint.
- Any other country: the generic figure with a pennant showing the country's initial.

## Files

PNGs are generated on first use and cached here. They are not committed.

- Neutral: `<country_slug>.png`, for example `canada.png`.
- Other expressions: `<country_slug>.<expression>.png`, for example `canada.sad.png`.
- Expressions: `neutral`, `smug`, `annoyed`, `tired`, `sad`.

Generated PNGs are ignored by the `.gitignore` file in this folder (`*.png`, `*.tmp`).

## Usage

```python
from autoshorts.longform.country_characters import character_for_country, draw_character

path = character_for_country("New Zealand", expression="smug")  # cached PNG path
image = draw_character("Canada", "sad")                          # PIL RGBA, not saved
```
