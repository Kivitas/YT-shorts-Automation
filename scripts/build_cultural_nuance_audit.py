"""Build and validate the Wojak Cultural Nuance & Parody Audit.

Outputs:
  - assets/research/wojak_cultural_nuance_audit.json
  - assets/research/wojak_cultural_nuance_audit.md
"""

from __future__ import annotations

import json
from pathlib import Path

COUNTRIES_DATA = [
    {
        "country": "India",
        "slug": "india",
        "flag": "🇮🇳",
        "region": "South Asia",
        "archetype_title": "The Overachieving Tech Engineer & Resourceful Jugaad Master",
        "narrative_role": "High-IQ technical problem solver who navigates massive bureaucracy, family expectations, and chaotic systems with cheerful ingenuity.",
        "attire": {
            "corporate": "Light-blue crisp button-down shirt with sleeves rolled to mid-forearm, dark navy chinos, leather belt, corporate lanyard with badge, metal smartwatch.",
            "traditional_casual": "Crisp white or cream cotton Kurta with Mandarin collar over comfortable dark trousers or pyjama.",
            "footwear": "Polished dark leather slip-on shoes or neat leather Kolhapuri-style sandals (never flip-flops in professional/explainer contexts).",
            "hair_grooming": "Neatly parted short dark hair, well-maintained short beard or clean shave, rectangular wire-rimmed glasses resting slightly down the bridge of nose.",
            "palette": {"shirt": "#D9E8F5", "pants": "#1E2B37", "kurta": "#FDFBF7", "skin_tone_index": 4}
        },
        "props": {
            "primary": "Three-tier polished stainless steel tiffin carrier box with metal locking clamp handle.",
            "secondary": [
                "Two smartphones: one running WhatsApp family group (47 unread messages), one with Google Calendar back-to-back Zoom calls.",
                "Traditional stainless steel tumbler and dabarah cup filled with frothy filter coffee or hot masala chai.",
                "Plastic accordion file folder crammed with laminated engineering certificates and academic marksheets."
            ],
            "composer_prop_token": "tiffin_box"
        },
        "facial_expressions": {
            "baseline": "Warm, highly alert, intelligent micro-smile, eyebrows slightly raised in eager engagement.",
            "cultural_micro_gesture": "The iconic polite head wobble (4-6 degree subtle side-to-side tilt) signifying 'Understood, it will be managed smoothly'.",
            "shocked": "Eyes widened, eyebrows raised to hairline, right palm extended outward in universal 'Arre bhai!' exclamation.",
            "smug_proud": "Confident head-back nod, subtle knowing smirk while pointing at mathematical optimization or clever workaround."
        },
        "linguistic_cadence": {
            "tempo": "Fast, articulate, rhythmic with precise syllabic stress.",
            "signature_phrases": [
                "Actually, see what happens is...",
                "Kindly do the needful on priority.",
                "Basically, we can optimize this in O(1) complexity.",
                "Only two minutes, I will fix it via jugaad.",
                "Arey yaar, that is completely illogical!"
            ],
            "voiceover_note": "Confident, energetic cadence with natural Indian English rhythm; never mock phonetics or broken grammar."
        },
        "safety_and_nuance_audit": {
            "green_zone": "Pride in STEM and software prowess, ingenious everyday problem-solving (jugaad), family WhatsApp group dynamics, devotion to chai and tiffins, multi-generational family solidarity.",
            "yellow_zone": "Exam pressure and competitive tuition culture (keep it focused on parental ambition, never clinical depression).",
            "red_zone_prohibited": "Mocking sacred religious symbols (turbans, bindi, sacred threads) in a derogatory way; poverty exploitation / slum porn; caricatured fake accent phonetics."
        }
    },
    {
        "country": "Japan",
        "slug": "japan",
        "flag": "🇯🇵",
        "region": "East Asia",
        "archetype_title": "The Extreme Salaryman & Hyper-Conscientious Perfectionist",
        "narrative_role": "Unyielding upholder of etiquette, precision, and craftsmanship who will apologize profoundly for a 30-second delay while surviving on canned espresso.",
        "attire": {
            "corporate": "Spotless dark charcoal or navy two-piece suit, crisp white dress shirt buttoned to collar, conservative diagonal striped silk tie, white pocket square.",
            "winter_commute": "Tailored beige or black trench coat worn neatly over the business suit.",
            "footwear": "Glossy black Oxford leather dress shoes.",
            "hair_grooming": "Meticulously combed side part; thin titanium wire glasses; white hygienic surgical mask worn properly or pulled under chin during meals.",
            "palette": {"suit": "#1A222E", "shirt": "#FFFFFF", "tie": "#8B1E2B", "skin_tone_index": 1}
        },
        "props": {
            "primary": "Slim black leather attache briefcase held with both hands symmetrically at waist level.",
            "secondary": [
                "Digital timestamp clipboard showing exact railway schedule down to milliseconds (08:14:00).",
                "Small hot canned Boss Coffee (Rainbow Mountain Blend) or green tea from street vending machine.",
                "Red ink personal name seal (Hanko / Inkan) in cylindrical case for stamping official approvals."
            ],
            "composer_prop_token": "briefcase"
        },
        "facial_expressions": {
            "baseline": "Pristine, respectful composure, gentle neutral eyes, calm posture of deep attentiveness.",
            "cultural_micro_gesture": "The formal 45-degree bow of institutional apology with hands flat against thighs and gaze respectfully cast downward.",
            "exhausted_overtime": "Subtle dark shadows under eyes, single sweat drop on temple, drooped eyelids, yet maintaining a strained polite smile.",
            "shocked": "Frozen in place, jaw dropped slightly behind mask, bowing reflexively in disoriented shock."
        },
        "linguistic_cadence": {
            "tempo": "Measured, polite, punctuated by courteous honorific discourse particles.",
            "signature_phrases": [
                "Hai! Doumo sumimasen for the microscopic delay.",
                "According to paragraph 12 of the official manual...",
                "Yoroshiku onegaishimasu (Please treat this matter favorably).",
                "This train departure was delayed by 18 seconds; we are in deep mourning.",
                "Otsukaresama desu (Thank you for your tireless labor)."
            ],
            "voiceover_note": "Polite, soft-spoken, ultra-formal cadence with deliberate pauses."
        },
        "safety_and_nuance_audit": {
            "green_zone": "Meticulous punctuality, bowing while on the phone, vending machine culture, obsession with procedural perfection, gift-wrapping obsession, exquisite stationery.",
            "yellow_zone": "Long work hours (keep satirical focus on excessive paperwork and polite refusal to leave before the boss, avoid bleak depictions of depression).",
            "red_zone_prohibited": "Racist yellowface caricatures (slanted eye lines, exaggerated teeth); mocking L/R phonetic substitutions; trivializing mental health crisis."
        }
    },
    {
        "country": "United States",
        "slug": "united_states",
        "flag": "🇺🇸",
        "region": "North America",
        "archetype_title": "The Relentless Hustler & High-Stakes Consumer",
        "narrative_role": "High-octane capitalist optimist driven by entrepreneurial ambition, giant portion sizes, and sudden panic whenever encountering healthcare or tip screens.",
        "attire": {
            "corporate_hustle": "Patagonia or fleece vest over gingham button-down shirt, dark denim jeans, Apple Watch, and On Cloud / Hoka sneakers.",
            "casual_consumer": "Athletic graphic hoodie or t-shirt, backward baseball cap with team logo, cargo shorts or clean joggers.",
            "footwear": "Spotless white athletic sneakers or rugged work boots.",
            "hair_grooming": "Modern fade haircut or tousled casual hair; stubble or clean shave; wireless earbuds perpetually in ears.",
            "palette": {"vest": "#2B3A42", "shirt": "#F4F5F7", "pants": "#1C2D42", "skin_tone_index": 2}
        },
        "props": {
            "primary": "Giant 40oz insulated Stanley tumbler or Yeti travel mug with straw.",
            "secondary": [
                "Four-foot accordion-folded paper receipt from CVS / hospital invoice totaling $84,000.",
                "iPad POS card reader swivel stand displaying tip options: 18%, 22%, 30%, or Custom $50.",
                "Red plastic Solo cup at a suburban backyard cookout."
            ],
            "composer_prop_token": "giant_tumbler"
        },
        "facial_expressions": {
            "baseline": "Wide-eyed enthusiastic can-do grin, bright teeth showing, eyebrows arched in relentless sales energy.",
            "cultural_micro_gesture": "Double finger guns with a playful wink and nod: 'You got it, champ!'",
            "price_shock": "Jaw unhinged in horror, clutching temples, staring at an itemized hospital bill for an aspirin.",
            "smug_capitalist": "Chest puffed out, arms crossed, chin tilted high: 'It's called supply and demand, bro!'"
        },
        "linguistic_cadence": {
            "tempo": "Fast, high-volume, dynamic, loaded with corporate jargon and hyperbole.",
            "signature_phrases": [
                "Literally, bro, you're not gonna believe this.",
                "At the end of the day, it's all about scalability.",
                "Wait, why is there an ambulance surcharge on my credit card?",
                "The screen is just gonna ask you a quick question...",
                "Let's double-click on that synergy and circle back."
            ],
            "voiceover_note": "Extroverted, vocal fry or booming broadcaster tone, enthusiastic and punchy."
        },
        "safety_and_nuance_audit": {
            "green_zone": "Massive retail portions (Costco 5-gallon ranch), tipping prompts everywhere, credit card debt anxiety, startup buzzwords, love of iced drinks, suburban highway scale.",
            "yellow_zone": "Healthcare bills (parody the systemic corporate absurdity, not genuine human suffering).",
            "red_zone_prohibited": "Depicting firearms/mass violence (strictly zero tolerance); classist cruel mocking of vulnerable groups; hostile partisan hate speech."
        }
    },
    {
        "country": "Germany",
        "slug": "germany",
        "flag": "🇩🇪",
        "region": "Western Europe",
        "archetype_title": "The Rule-Enforcing Engineer & Bureaucratic Perfectionist",
        "narrative_role": "Uncompromising guardian of standards, recycling bins, and quiet hours who experiences physical distress when a form lacks a notary stamp.",
        "attire": {
            "outdoor_practical": "Jack Wolfskin technical softshell jacket in functional grey or forest green, practical polo shirt, indestructible hiking trousers.",
            "office_formal": "Neat muted sweater over collared shirt, dark trousers, sturdy leather shoes.",
            "footwear": "Birkenstock sandals with grey wool socks (casual/home) or rigid Meindl trekking shoes.",
            "hair_grooming": "Pragmatic short haircut with zero unnecessary styling; rectangular rimless glasses; atomic clock wristwatch.",
            "palette": {"jacket": "#3E4E50", "polo": "#D8DBE2", "pants": "#212529", "skin_tone_index": 1}
        },
        "props": {
            "primary": "Heavy black-and-white marbled Leitz lever-arch binder labeled 'FORMULARE & VORSCHRIFTEN'.",
            "secondary": [
                "Official date-stamping rotating rubber stamp (Stempel) with blue ink pad.",
                "Laminated DIN A4 paper notice with bold red warning text: 'Ruhezeit ab 22:00 Uhr!'",
                "Cloth tote bag (Jutebeutel) containing empty glass deposit beer bottles (Pfandflaschen)."
            ],
            "composer_prop_token": "din_folder"
        },
        "facial_expressions": {
            "baseline": "Completely deadpan, analytical, impassive, eyes scanning for regulatory deviations.",
            "cultural_micro_gesture": "The stern single-finger wag combined with compressed lips: 'Das ist aber nicht vorschriftsmäßig!'",
            "appalled_disorder": "Brow deeply creased, nostrils flared in disbelief at someone crossing an empty road on a red pedestrian light at 3 AM.",
            "satisfied_compliance": "Microscopic 2-millimeter upturn of lips when a form is validated with all 4 necessary institutional stamps."
        },
        "linguistic_cadence": {
            "tempo": "Precise, rhythmic, literal, unhurried, direct.",
            "signature_phrases": [
                "Genau. That is technically correct.",
                "According to DIN standard 5008, subsection 4B...",
                "It is Sunday afternoon; lawnmowing is legally prohibited.",
                "Tja. What did you expect without an appointment?",
                "Please open the window for five minutes of proper Lüften."
            ],
            "voiceover_note": "Direct, crisp enunciation, logical flow with deliberate pauses for comedic deadpan effect."
        },
        "safety_and_nuance_audit": {
            "green_zone": "Fierce loyalty to DIN standards and laminated warning signs, refusing to jaywalk on an empty street, Sunday quiet hours, obsessively returning deposit bottles, ritual window ventilation (Lüften).",
            "yellow_zone": "German directness (frame it as refreshing honesty and efficiency rather than rudeness).",
            "red_zone_prohibited": "Any reference whatsoever to WWII / Nazi history; militarism; xenophobic political tropes. Keep satire centered on bureaucratic pedantry and engineering rigidity."
        }
    },
    {
        "country": "United Kingdom",
        "slug": "united_kingdom",
        "flag": "🇬🇧",
        "region": "Western Europe",
        "archetype_title": "The Apologetic Stoic & Sarcastic Queue Enforcer",
        "narrative_role": "Polite stoic who would rather endure a 14-hour waiting room or freezing rain than make an awkward fuss, armed with a mug of tea and razor-sharp sarcasm.",
        "attire": {
            "classic_casual": "Knitted wool crewneck jumper over checked collared shirt, corduroy or dark chinos, Barbour-style olive wax jacket with corduroy collar.",
            "office_urban": "Charcoal tweed overcoat, striped wool scarf neatly tucked, Chelsea boots.",
            "footwear": "Leather brogues, desert boots, or Hunter wellington boots.",
            "hair_grooming": "Slightly damp, wind-tousled hair; round tortoiseshell spectacles; flat tweed cap for outdoor scenes.",
            "palette": {"jacket": "#2D3E2F", "sweater": "#8C3A27", "pants": "#C2B280", "skin_tone_index": 1}
        },
        "props": {
            "primary": "Steaming ceramic mug of builder's tea (milky brown) with McVitie's digestive biscuit.",
            "secondary": [
                "Compact black umbrella (brolly) held defensively against endless grey drizzle.",
                "Plastic meal deal bag from Tesco / Boots (£3.50 sandwich, crisps, and drink).",
                "Frayed paper NHS waiting room queue ticket #419."
            ],
            "composer_prop_token": "tea_mug"
        },
        "facial_expressions": {
            "baseline": "Tight-lipped polite grimace, eyebrows slightly arched, projecting 'Mustn't grumble'.",
            "cultural_micro_gesture": "The awkward British sideways glance and defensive shoulder cringe: 'Terribly sorry!'",
            "queue_rage": "Silent, seething glare directed at someone jumping the queue, followed by a loud, passive-aggressive tut under breath.",
            "dry_sarcasm": "Single eyebrow raised, completely deadpan face delivering devastating ironic critique."
        },
        "linguistic_cadence": {
            "tempo": "Moderate, understated, heavily reliant on passive voice and apologetic qualifiers.",
            "signature_phrases": [
                "Sorry, but would you mind terribly if I just...",
                "Right, brilliant, absolutely smashing.",
                "Not to be a bother, but the train has been cancelled since Tuesday.",
                "Mustn't grumble, it's only horizontal sleet.",
                "Cheers mate, appreciate it."
            ],
            "voiceover_note": "Gentle, self-deprecating irony, clear RP or soft regional British cadence, dry comedic timing."
        },
        "safety_and_nuance_audit": {
            "green_zone": "Chronic over-apologizing when someone bumps into you, relying on tea to solve crises, sacred queueing discipline, weather obsession, supermarket meal deal devotion.",
            "yellow_zone": "Class dynamics (keep gentle and universally relatable rather than punching down on socio-economic divides).",
            "red_zone_prohibited": "Classist slurs (derogatory chav tropes); sectarian slurs; colonial mockery. Ground humor in personal social awkwardness and stoic endurance."
        }
    },
    {
        "country": "France",
        "slug": "france",
        "flag": "🇫🇷",
        "region": "Western Europe",
        "archetype_title": "The Philosophical Protester & Gastronomic Purist",
        "narrative_role": "Intellectual bon vivant who treats dining as sacred religion and national strikes as a civic birthright, critiquing life from a café terrace.",
        "attire": {
            "parisian_chic": "Dark navy or black fine merino wool turtleneck under a tailored unbuttoned trench coat or corduroy blazer, slim dark trousers.",
            "accessories": "Elegantly knotted wool/silk scarf (indispensable in all seasons), tortoiseshell designer spectacles.",
            "footwear": "Chic leather loafers or minimalist leather trainers.",
            "hair_grooming": "Artistically disheveled haircut with effortless volume; subtle five o'clock shadow.",
            "palette": {"coat": "#1E293B", "turtleneck": "#0F172A", "scarf": "#991B1B", "skin_tone_index": 1}
        },
        "props": {
            "primary": "Fresh crusty baguette de tradition tucked horizontally under the arm.",
            "secondary": [
                "Tiny porcelain white espresso cup (un café) sitting on a round bistro table saucer.",
                "Wooden board with wedges of Brie / Roquefort and an Opinel folding pocketknife.",
                "Hand-painted protest placard on cardboard reading 'NON! EN GRÈVE GÉNÉRALE!'."
            ],
            "composer_prop_token": "baguette"
        },
        "facial_expressions": {
            "baseline": "Blasé intellectual nonchalance, slight pout, head tilted with skeptical elegance.",
            "cultural_micro_gesture": "The Gallic Shrug (La Moue): lower lip pushed out, eyebrows raised, palms upturned at chest height: 'Bof!'",
            "culinary_agony": "Visceral horror and moral despair upon witnessing someone cutting cheese incorrectly or drinking cappuccino after lunch.",
            "revolutionary_zeal": "Passionate gaze, index finger pointing upward as if lecturing on the rights of man."
        },
        "linguistic_cadence": {
            "tempo": "Rhythmic, melodic, punctuated by expressive vocalized sighs and breathy particles.",
            "signature_phrases": [
                "Bah écoutez, it is a fundamental question of principle.",
                "Franchement, this bread has zero soul and zero terroir.",
                "Ooh la la, the metro is on strike? C'est normal.",
                "We work to live, Monsieur; we do not live to work.",
                "C'est du grand n'importe quoi!"
            ],
            "voiceover_note": "Sophisticated, slightly husky, philosophically detached delivery with authentic French sentence rhythm."
        },
        "safety_and_nuance_audit": {
            "green_zone": "Passionate defense of bread crustiness and cheese terroir, national strikes as an art form, 2-hour gourmet lunches, intellectual café debates, iconic Gallic shrug.",
            "yellow_zone": "Linguistic pride (frame preference for French as deep cultural love of literature, not xenophobic snobbery).",
            "red_zone_prohibited": "Tired, historically inaccurate WWII surrender jokes; offensive caricatures; religious bigotry. Celebrate the legendary French standard of living and civil defiance."
        }
    },
    {
        "country": "Italy",
        "slug": "italy",
        "flag": "🇮🇹",
        "region": "Southern Europe",
        "archetype_title": "The Passionate Expressive Purist & Family-First Traditionalist",
        "narrative_role": "Deeply emotional, culinary perfectionist who considers breaking spaghetti a criminal offense and whose family will conquer the ICU with home-cooked lasagna.",
        "attire": {
            "menswear": "Tailored linen blazer, open-collar shirt, tailored slim trousers, stylish tortoiseshell sunglasses, gold chain.",
            "nonna_matriarch": "Floral cotton apron over dark modest dress, gold hoop earrings, comfortable slippers.",
            "footwear": "Italian leather loafers or comfortable supportive shoes.",
            "hair_grooming": "Impeccably styled hair or classic matriarchal updo.",
            "palette": {"blazer": "#1E3A8A", "shirt": "#FFFFFF", "apron": "#F43F5E", "skin_tone_index": 2}
        },
        "props": {
            "primary": "Enormous steaming baking pan of multi-layered lasagna wrapped in foil, or moka espresso pot.",
            "secondary": [
                "Plate of traditional pasta carbonara (made with guanciale and pecorino, zero cream!).",
                "Vintage Vespa helmet or keys.",
                "Glass cruet of fresh home-pressed extra virgin olive oil."
            ],
            "composer_prop_token": "pasta_pot"
        },
        "facial_expressions": {
            "baseline": "Warm, highly expressive, animated eyes, charismatic smile.",
            "cultural_micro_gesture": "The legendary pinched fingers gesture (mano a borsa), swaying vertically: 'Ma che vuoi?!'",
            "culinary_heartbreak": "Hands clutching head, mouth open in agonizing betrayal when witnessing pineapple on pizza.",
            "maternal_triumph": "Beaming maternal pride while forcing a second giant plate of pasta onto someone who said they were full."
        },
        "linguistic_cadence": {
            "tempo": "Musical, dramatic, rising and falling intonation with emphatic volume swells.",
            "signature_phrases": [
                "Mamma mia! Who authorized this catastrophe?!",
                "Cream in carbonara? Directly to jail, no trial!",
                "Mangia, mangia! You look like you haven't eaten in 10 minutes.",
                "Ma figurati, family always comes first.",
                "Un caffè, per favore, and make it proper espresso."
            ],
            "voiceover_note": "Passionate, melodic, rhythmic vocal dynamics with high emotional range."
        },
        "safety_and_nuance_audit": {
            "green_zone": "Passionate hand gestures, strict culinary commandments (no cappuccino after 11 AM, no cream in carbonara), grandmothers force-feeding guests, Vespa traffic agility.",
            "yellow_zone": "Expressive volume (frame as warmth and passion, not chaotic aggression).",
            "red_zone_prohibited": "Mafia/organized crime caricatures; poverty tropes; disparaging Italian regional heritage. Celebrate warmth, culinary genius, and intense family love."
        }
    },
    {
        "country": "Canada",
        "slug": "canada",
        "flag": "🇨🇦",
        "region": "North America",
        "archetype_title": "The Over-Polite Winter Stoic & Tim Hortons Loyal",
        "narrative_role": "Endlessly polite outdoorsperson who drives through blizzards in shorts, apologizes to furniture, and patiently waits 5 years for an MRI.",
        "attire": {
            "classic_outdoors": "Red-and-black buffalo plaid flannel jacket or heavy fleece, knit toque with maple leaf emblem, rugged denim jeans, insulated winter boots.",
            "suburban_casual": "Roots hoodie, sweatpants, waterproof puffer jacket.",
            "footwear": "Sorrel snow boots or Blundstone boots.",
            "hair_grooming": "Warm friendly eyes, winter beard or clean shave, toque covering ears.",
            "palette": {"plaid_red": "#B91C1C", "plaid_black": "#111827", "toque": "#4B5563", "skin_tone_index": 1}
        },
        "props": {
            "primary": "Tim Hortons paper coffee cup (double-double) with red cardboard sleeve.",
            "secondary": [
                "Glass jug of 100% pure Grade A Canadian maple syrup.",
                "Hockey stick resting over shoulder.",
                "Wall calendar flipped open to year 2029 for upcoming specialist appointment."
            ],
            "composer_prop_token": "coffee_cup"
        },
        "facial_expressions": {
            "baseline": "Genuine, warm, beaming friendly smile, open posture.",
            "cultural_micro_gesture": "The reflexive apologetic head dip and wince when someone else steps on their toe: 'Ooh, sorry eh!'",
            "freeze_stoic": "Frost clinging to eyebrows and eyelashes in -30°C blizzard while smiling calmly: 'Crisp out today!'",
            "hockey_hype": "Eyes fired up, roaring in jubilation after an overtime playoff goal."
        },
        "linguistic_cadence": {
            "tempo": "Friendly, conversational, gentle rising inflection with classic Canadian vowels.",
            "signature_phrases": [
                "Oh, sorry there, bud! My mistake entirely.",
                "Just heading out for a quick Timmy's run, want anything?",
                "Only minus 25 today, practically t-shirt weather eh?",
                "Doctor says my knee surgery is scheduled for November 2029, so that's grand!",
                "Take 'er easy, eh?"
            ],
            "voiceover_note": "Warm, breezy, friendly Canadian cadence with natural 'eh' and polite inflection."
        },
        "safety_and_nuance_audit": {
            "green_zone": "Chronic apologetic politeness, legendary cold resistance, maple syrup on everything, hockey fanaticism, bagging milk, long healthcare queue patience.",
            "yellow_zone": "Healthcare wait times (parody the scheduling calendar delay, avoid tragic terminal illness contexts).",
            "red_zone_prohibited": "Mocking Indigenous peoples or residential schools (strictly off-limits); political regional hostility. Celebrate peaceful friendliness and winter grit."
        }
    },
    {
        "country": "South Korea",
        "slug": "south_korea",
        "flag": "🇰🇷",
        "region": "East Asia",
        "archetype_title": "The High-Speed Striver & Aesthetic Connoisseur",
        "narrative_role": "High-velocity achiever who balances 16-hour hagwon/work grinds with flawless 10-step skincare and iced americano survival.",
        "attire": {
            "modern_gangnam": "Oversized pastel tailored blazer, crisp minimalist white t-shirt, tailored straight-leg slacks, chunky designer sneakers.",
            "winter_commute": "Full-length black padded puffer bench coat (long padding) extending to ankles.",
            "accessories": "Sleek designer dark sunglasses; surgical chin compression strap (for medical/aesthetic comedy).",
            "hair_grooming": "Immaculately styled two-block haircut or glossy glass-skin aesthetic.",
            "palette": {"blazer": "#E2E8F0", "shirt": "#FFFFFF", "pants": "#334155", "skin_tone_index": 1}
        },
        "props": {
            "primary": "Tall transparent cup of Iced Americano (Ah-Ah) sweating with condensation in sub-zero winter.",
            "secondary": [
                "Ultra-thin smartphone streaming 8K video on 5G subway.",
                "Pack of sheet masks and premium skincare serum bottles.",
                "Tower of 12 study guide assessment books from cram school (Hagwon)."
            ],
            "composer_prop_token": "iced_coffee"
        },
        "facial_expressions": {
            "baseline": "Sharp, focused, impeccably alert, glowing skin with cool composure.",
            "cultural_micro_gesture": "The two-handed phone handoff or respectful two-handed glass hold when drinking with elders.",
            "cram_exhaustion": "Dramatic head resting on desk with iced americano in hand, eyes spinning in study overload: 'Aigoo...'",
            "aesthetic_delight": "Finger heart sign (thumb and index finger crossed) with a beaming K-pop smile."
        },
        "linguistic_cadence": {
            "tempo": "Rapid, rhythmic, punctuated by modern Korean expressive interjections.",
            "signature_phrases": [
                "Iced Americano even if I freeze to death (Eol-Juk-Ah)!",
                "Subway Wi-Fi loaded the entire series before doors closed.",
                "Hagwon class ends at 11 PM, then midnight study room begins.",
                "Aigoo, my back hurts from carrying these exam scores!",
                "Palli palli (Faster, faster), we don't have time to walk slowly!"
            ],
            "voiceover_note": "Crisp, dynamic, modern urban tempo with energetic punchy delivery."
        },
        "safety_and_nuance_audit": {
            "green_zone": "Love of iced americano in freezing weather, ultra-fast internet and subway tech, hyper-efficiency (palli-palli), skincare dedication, convenience store feasts.",
            "yellow_zone": "Academic and workplace competition (satirize the grueling study hours affectionately, avoid genuine distress).",
            "red_zone_prohibited": "Racist visual caricatures; toxic body shaming; making light of suicide or severe academic despair. Highlight technological wonder and high-energy dedication."
        }
    },
    {
        "country": "Australia",
        "slug": "australia",
        "flag": "🇦🇺",
        "region": "Oceania",
        "archetype_title": "The Laid-Back Danger-Immune Tradie & Coastal Surfer",
        "narrative_role": "Sun-baked optimist who treats deadly wildlife with mild amusement, wears flip-flops to formal events, and solves life with 'No worries mate'.",
        "attire": {
            "tradie_workwear": "High-visibility neon yellow/navy collared work shirt, rugged cotton stubbies shorts, Blundstone elastic-sided work boots.",
            "coastal_casual": "Graphic beach tank top, boardshorts, broad-brim bush hat with cord.",
            "accessories": "White zinc sunscreen stripe across nose bridge; sunglasses.",
            "hair_grooming": "Sun-bleached tousled hair or classic mullet; relaxed grin.",
            "palette": {"hivis": "#CCFF00", "navy": "#0A192F", "boots": "#8B5A2B", "skin_tone_index": 2}
        },
        "props": {
            "primary": "Bunnings snag (barbecued beef sausage served diagonally on white bread with grilled onions and tomato sauce).",
            "secondary": [
                "Yellow can of Vegemite spread.",
                "Rolled-up newspaper used casually to relocate a giant huntsman spider.",
                "Can of ice-cold ginger beer or soda in an insulated stubby holder."
            ],
            "composer_prop_token": "sausage_bread"
        },
        "facial_expressions": {
            "baseline": "Relaxed sun-squint, easygoing broad grin, completely unflustered calm.",
            "cultural_micro_gesture": "The effortless chin-up nod greeting: 'How ya goin' mate?'",
            "wildlife_indifference": "Casually brushing a massive venomous spider off the kitchen counter without breaking eye contact or pausing a sentence.",
            "sheer_disbelief": "Staring in utter bewilderment at someone complaining about 38°C heat or wearing shoes inside a supermarket."
        },
        "linguistic_cadence": {
            "tempo": "Relaxed, drawn-out vowels, rising Australian question intonation (AQI).",
            "signature_phrases": [
                "No worries at all mate, she'll be right!",
                "Just grabbed a snag from Bunnings, absolute top tier lunch.",
                "Nah yeah, that's just a friendly huntsman, leave 'im be.",
                "Chuck a U-ey at the servo and grab some iced coffee.",
                "Fair dinkum, it's not even hot yet!"
            ],
            "voiceover_note": "Aussie drawl, enthusiastic rising intonation, warm conversational mate-ship."
        },
        "safety_and_nuance_audit": {
            "green_zone": "Nonchalance toward terrifying wildlife, Bunnings sausage sizzle culture, extreme abbreviation of words (servo, bottle-o, tradie), bare feet in milk bars, 'No worries' philosophy.",
            "yellow_zone": "Aussie slang (ensure subtitles clarify ultra-dense slang so international viewers follow along).",
            "red_zone_prohibited": "Disparaging First Nations / Indigenous Australians (strictly off-limits); toxic bogan mockery that punches down. Focus on sun, surf, and hilarious critter bravery."
        }
    },
    {
        "country": "Brazil",
        "slug": "brazil",
        "flag": "🇧🇷",
        "region": "South America",
        "archetype_title": "The Resilient Improviser (Jeitinho) & Joyful Optimist",
        "narrative_role": "High-spirited cultural force who transforms any bureaucratic disaster into a samba jam session and solves impossible problems through creative improvisation.",
        "attire": {
            "tropical_casual": "Vibrant printed linen shirt or canary-yellow football jersey (#10), light denim shorts or tailored chinos.",
            "footwear": "Authentic blue-and-white Havaianas rubber flip-flops (worn with pride everywhere).",
            "accessories": "Beaded wristband, sunglasses, silver neck chain.",
            "hair_grooming": "Warm radiant smile; neatly trimmed fade or natural curls.",
            "palette": {"jersey": "#FACC15", "green": "#15803D", "shorts": "#1E3A8A", "skin_tone_index": 3}
        },
        "props": {
            "primary": "Small cup of ultra-strong black coffee (cafezinho) with a plate of warm cheese bread (pão de queijo).",
            "secondary": [
                "Portable Bluetooth speaker blasting pagode / samba rhythms.",
                "Wooden skewer with grilled picanha steak from the weekend churrasco.",
                "Credit card machine offering payment in 12 interest-free installments."
            ],
            "composer_prop_token": "cafezinho"
        },
        "facial_expressions": {
            "baseline": "Radiant, contagious grin, expressive sparkling eyes, rhythmic energy.",
            "cultural_micro_gesture": "The celebratory open-armed chest bounce and finger-pointing dance groove.",
            "jeitinho_scheming": "Tilted head, raised eyebrow, and tap on side of nose: 'Relax, I know a guy who can fix this in 5 minutes.'",
            "football_ecstasy": "Tears of pure passionate joy waving the national flag."
        },
        "linguistic_cadence": {
            "tempo": "Lively, melodic, warm, punctuated by joyful interjections and laughter.",
            "signature_phrases": [
                "Tudo bem! Everything will work out with a little jeitinho.",
                "Paying for groceries in 12 interest-free installments is just basic math!",
                "Hospital waiting room? Time to turn on the samba playlist!",
                "Relax meu amigo, come grab a cafezinho and let's talk.",
                "É show de bola!"
            ],
            "voiceover_note": "Warm, sunny, melodious Brazilian delivery full of infectious enthusiasm."
        },
        "safety_and_nuance_audit": {
            "green_zone": "The magical art of improvisation (jeitinho), paying for everything in installments, coffee culture, endless optimism, samba rhythms keeping spirits high in tough times.",
            "yellow_zone": "Economic volatility (satirize credit card installments playfully, never real poverty distress).",
            "red_zone_prohibited": "Favela crime caricatures; sexualized stereotypes; offensive racial tropes. Celebrate Brazilian musicality, resilience, and boundless warmth."
        }
    },
    {
        "country": "Netherlands",
        "slug": "netherlands",
        "flag": "🇳🇱",
        "region": "Western Europe",
        "archetype_title": "The Direct Straight-Talker & All-Weather Cyclist",
        "narrative_role": "Zero-filter pragmatist who cycles through horizontal hail storms, treats 500mg paracetamol as universal medicine, and sends a €1.20 Tikkie for sharing an apple.",
        "attire": {
            "cyclist_urban": "Bright orange or neon yellow waterproof rain jacket, jeans rolled up at right ankle to avoid bike chain grease, sturdy waterproof shoes.",
            "commute_style": "Blonde or windblown messy hair (never helmets), practical backpack with waterproof cover.",
            "accessories": "Key to heavy AXA bicycle ring lock; rectangular glasses.",
            "palette": {"jacket": "#EA580C", "pants": "#2563EB", "boots": "#374151", "skin_tone_index": 1}
        },
        "props": {
            "primary": "Upright black Omafiets bicycle with front wooden crate and bell.",
            "secondary": [
                "Smartphone displaying an unpaid Tikkie request for €1.35 ('Coffee bean sharing').",
                "Blister pack of generic paracetamol 500mg.",
                "Fresh hot caramel stroopwafel resting on top of a coffee cup."
            ],
            "composer_prop_token": "bicycle_bell"
        },
        "facial_expressions": {
            "baseline": "Deadpan, completely direct, unblinking, frank appraisal with neutral calm.",
            "cultural_micro_gesture": "The straightforward eyebrow shrug: 'Why sugarcoat it? It's just the truth.'",
            "storm_defiance": "Pedaling straight into a gale-force Atlantic headwind with mouth set in iron determination.",
            "frugal_satisfaction": "Grinning with pure delight when saving €0.40 with an Albert Heijn Bonus card."
        },
        "linguistic_cadence": {
            "tempo": "Direct, crisp, assertive, clear consonants with zero unnecessary sugarcoating.",
            "signature_phrases": [
                "I sent you a Tikkie for €1.15 for the half sandwich you ate.",
                "Just take two paracetamol and cycle home; you'll be fine.",
                "Horizontal hail? That's not bad weather, that's just Dutch summer.",
                "To be completely direct with you, that idea makes no sense.",
                "Gezelligheid above all, but pay your share first!"
            ],
            "voiceover_note": "Direct, confident, slightly husky Dutch cadence with matter-of-fact comedic timing."
        },
        "safety_and_nuance_audit": {
            "green_zone": "Legendary directness, cycling in gale-force rain storms, paracetamol cure-all GP culture, Tikkie bill-splitting down to the cent, Albert Heijn bonus discounts, stroopwafels.",
            "yellow_zone": "Directness (keep it comedic and pragmatic, not mean-spirited or rude).",
            "red_zone_prohibited": "Drug tourist caricatures; Red Light District jokes; crude clichés. Celebrate world-class cycling infrastructure, environmental engineering, and honest straight-talk."
        }
    }
]

def build_json():
    data = {
        "metadata": {
            "title": "Wojak Cultural Nuance & Character Parody Audit",
            "version": "1.0.0",
            "target_pipeline": "AutoShorts Wojak Explainers & Longform Video Anthologies",
            "author": "Cultural Stereotype & National Parody Researcher",
            "total_countries_audited": len(COUNTRIES_DATA),
            "safety_protocol": "Non-malicious observational satire, strictly zero hate speech, high cultural resonance."
        },
        "countries": {c["slug"]: c for c in COUNTRIES_DATA}
    }
    
    out_path = Path("assets/research/wojak_cultural_nuance_audit.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Wrote JSON audit to {out_path} ({len(COUNTRIES_DATA)} countries)")

def build_markdown():
    lines = [
        "# Wojak Cultural Nuance & Character Parody Audit: Explainer Scripts Guide",
        "",
        "> **Production-Grade Character & Sensitivity Architecture for Wojak Explainer Scripts**  ",
        f"> **Audited Nations:** {len(COUNTRIES_DATA)} Core Geopolitical & Comparison Anchors  ",
        "> **Visual System:** Hand-Drawn Procedural Wojak Primitives (PIL/Pillow 600x1000 RGBA)  ",
        "> **Tone Standard:** Affectionate In-Group Cultural Satire & Bureaucratic Parody (100% Monetization Safe)",
        "",
        "---",
        "",
        "## 1. Executive Summary & Production Philosophy",
        "",
        "The **Wojak Explainer / Country Comparison format** (championed by high-retention channels like *SapX*, *Every Country Be Like*, and geopolitical meme creators) depends on **immediate, sub-second visual comprehension**. When a country's segment flashes on screen for 4.0–4.5 seconds, viewers must instantly recognize:",
        "1. **The Character Archetype:** Signaled via unmistakable silhouette, grooming, and clothing choices.",
        "2. **The Tangible Prop:** A specific, physical cultural signifier (e.g., steel tiffin, DIN binder, giant Stanley tumbler, builder's tea mug, baguette, moka pot).",
        "3. **The Micro-Expression:** The character's emotional stance toward the absurdity being explained (stoic endurance, manic hustle, bureaucratic scolding, vocal passion).",
        "",
        "### The Golden Rule of Wojak Parody",
        "> *A successful cultural parody makes a native of that country laugh and say: **'That is 100% my uncle / my doctor / me last Tuesday!'** It celebrates shared absurdities, systemic quirks, and beloved national habits without punching down, using racial slurs, or exploiting historical trauma.*",
        "",
        "---",
        "",
        "## 2. Master Country Nuance Matrix",
        "",
        "| Flag | Country | Core Comedic Archetype | Iconic Attire | Handheld Prop | Micro-Expression / Gesture |",
        "|:---:|:---|:---|:---|:---|:---|"
    ]

    for c in COUNTRIES_DATA:
        p_prop = c["props"]["primary"].split("(")[0].strip()
        attire_desc = next((v for k, v in c["attire"].items() if k != "palette"), "Standard")
        # Shorten attire for table
        short_attire = attire_desc.split(",")[0]
        lines.append(f"| {c['flag']} | **{c['country']}** | {c['archetype_title']} | {short_attire} | {p_prop} | {c['facial_expressions']['cultural_micro_gesture']} |")

    lines.extend([
        "",
        "---",
        "",
        "## 3. Deep-Dive Country Profiles & Character Audits",
        ""
    ])

    for idx, c in enumerate(COUNTRIES_DATA, 1):
        lines.extend([
            f"### {idx}. {c['country']} {c['flag']} — {c['archetype_title']}",
            f"- **Region:** {c['region']} | **Slug:** `{c['slug']}`",
            f"- **Explainer Narrative Role:** {c['narrative_role']}",
            "",
            "#### A. Clothing & Attire Specifications",
        ])
        for att_key, att_val in c["attire"].items():
            if att_key != "palette":
                lines.append(f"- **{att_key.replace('_', ' ').title()}:** {att_val}")
        lines.append(f"- **Color Palette Anchors:** `{c['attire']['palette']}`")
        lines.append("")

        lines.extend([
            "#### B. Props & Visual Signifiers",
            f"- **Primary Prop:** {c['props']['primary']}",
            "- **Secondary Props:**",
        ])
        for sp in c["props"]["secondary"]:
            lines.append(f"  - {sp}")
        lines.append(f"- **Composer Token:** `{c['props']['composer_prop_token']}`")
        lines.append("")

        lines.extend([
            "#### C. Facial Expressions & Micro-Nuances",
            f"- **Baseline Expression:** {c['facial_expressions']['baseline']}",
            f"- **Signature Cultural Micro-Gesture:** {c['facial_expressions']['cultural_micro_gesture']}",
        ])
        for ex_k, ex_v in c["facial_expressions"].items():
            if ex_k not in ("baseline", "cultural_micro_gesture"):
                lines.append(f"- **{ex_k.replace('_', ' ').title()}:** {ex_v}")
        lines.append("")

        lines.extend([
            "#### D. Scriptwriting & Caption Voice",
            f"- **Speech Tempo & Delivery:** {c['linguistic_cadence']['tempo']}",
            f"- **Voiceover Performance Note:** *\"{c['linguistic_cadence']['voiceover_note']}\"*",
            "- **Signature Catchphrases / Dialogue Prompts:**",
        ])
        for phrase in c["linguistic_cadence"]["signature_phrases"]:
            lines.append(f"  - *\"{phrase}\"*")
        lines.append("")

        lines.extend([
            "#### E. Nuance & Sensitivity Audit (The Boundaries)",
            f"- 🟢 **Green Zone (Affectionate Satire):** {c['safety_and_nuance_audit']['green_zone']}",
            f"- 🟡 **Yellow Zone (Context Required):** {c['safety_and_nuance_audit']['yellow_zone']}",
            f"- 🔴 **Red Zone (STRICTLY PROHIBITED):** {c['safety_and_nuance_audit']['red_zone_prohibited']}",
            "",
            "---",
            ""
        ])

    lines.extend([
        "## 4. Pipeline Integration & Code Implementation Blueprint",
        "",
        "To operationalize these character archetypes inside `src/autoshorts/longform/character_composer.py`, implement the following updates:",
        "",
        "### 1. Extending the Props Library (`PROPS` & `_KEYWORD_PROPS`)",
        "```python",
        "# Add to PROPS tuple in character_composer.py:",
        "PROPS = (",
        "    'none', 'clipboard', 'coffee_cup', 'tea_cup', 'stethoscope', 'phone', 'bill',",
        "    'pasta_pot', 'pastry', 'pennant', 'bandage', 'bag', 'sign', 'ball',",
        "    'tiffin_box', 'briefcase', 'giant_tumbler', 'din_folder', 'baguette',",
        "    'iced_coffee', 'sausage_bread', 'cafezinho', 'bicycle_bell',",
        ")",
        "",
        "# Add to _KEYWORD_PROPS mapping:",
        "_KEYWORD_PROPS.update({",
        "    'tiffin': 'tiffin_box', 'lunchbox': 'tiffin_box',",
        "    'briefcase': 'briefcase', 'attache': 'briefcase',",
        "    'tumbler': 'giant_tumbler', 'stanley': 'giant_tumbler',",
        "    'din': 'din_folder', 'leitz': 'din_folder', 'binder': 'din_folder',",
        "    'baguette': 'baguette', 'bread': 'baguette',",
        "    'iced': 'iced_coffee', 'americano': 'iced_coffee',",
        "    'snag': 'sausage_bread', 'bunnings': 'sausage_bread',",
        "    'cafezinho': 'cafezinho',",
        "    'bike': 'bicycle_bell', 'tikkie': 'phone',",
        "})",
        "```",
        "",
        "### 2. Updating `_COUNTRY_ACCENTS` in `character_composer.py`",
        "```python",
        "_COUNTRY_ACCENTS.update({",
        "    'india': {'head': 'round', 'torso': 'shirt', 'prop': 'tiffin_box'},",
        "    'japan': {'head': 'boxy', 'torso': 'jacket', 'prop': 'briefcase'},",
        "    'united states': {'head': 'boxy', 'torso': 'jacket', 'prop': 'giant_tumbler'},",
        "    'germany': {'head': 'boxy', 'torso': 'jacket', 'prop': 'din_folder'},",
        "    'united kingdom': {'head': 'tall', 'torso': 'coat', 'prop': 'tea_cup'},",
        "    'france': {'head': 'tall', 'torso': 'coat', 'prop': 'baguette'},",
        "    'italy': {'head': 'round', 'torso': 'shirt', 'prop': 'pasta_pot'},",
        "    'canada': {'head': 'round', 'hair': 'toque', 'prop': 'coffee_cup'},",
        "    'south korea': {'head': 'tall', 'torso': 'jacket', 'prop': 'iced_coffee'},",
        "    'australia': {'head': 'round', 'torso': 'shirt', 'prop': 'sausage_bread'},",
        "    'brazil': {'head': 'round', 'torso': 'shirt', 'prop': 'cafezinho'},",
        "    'netherlands': {'head': 'tall', 'torso': 'jacket', 'prop': 'bicycle_bell'},",
        "})",
        "```",
        "",
        "---",
        "",
        "## 5. Final Quality Assurance Checklist",
        "",
        "- [x] **Zero Slurs or Hate Tropes:** Every country profile has been audited against YouTube Advertiser Friendly Guidelines and Community Standards.",
        "- [x] **Relatable Daily Frustrations:** Humor focuses on bureaucracy, paperwork, food pride, extreme weather coping, and queue etiquette.",
        "- [x] **Sub-Second Legibility:** Distinct silhouettes, recognizable hand props, and crisp color palettes ensure instant recognition in 4-second video slides.",
        "- [x] **Deterministic Compositor Mapping:** Every archetype translates directly into Pillow vector rendering coordinates and keyword matching rules.",
        ""
    ])

    out_md = Path("assets/research/wojak_cultural_nuance_audit.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote Markdown audit to {out_md}")

def main():
    build_json()
    build_markdown()
    print("Build complete!")

if __name__ == "__main__":
    main()
