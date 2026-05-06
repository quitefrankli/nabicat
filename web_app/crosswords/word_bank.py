"""
Static data and validators for the crossword subapp.

* ``DEBUG_SETS`` - hand-picked (theme, difficulty) -> 5-word fixtures
  used by ``DebugSource`` in debug/test mode.
* ``FALLBACK_POOL`` - modern general-purpose pool used by
  ``FallbackSource`` when the primary source returns nothing.
* ``validate_theme`` / ``clamp_difficulty`` - input validators used by
  the Flask route before any word source is called.

Any source of words (including Meridian) should keep vocabulary modern
and familiar to a person in their 20s - avoid archaic or obscure words.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from web_app.config import ConfigManager

WordClue = Tuple[str, str]


def theme_criteria() -> str:
    cfg = ConfigManager()
    return (
        f"Theme must be a single word "
        f"({cfg.crosswords_theme_min_len}-{cfg.crosswords_theme_max_len} letters, "
        "no spaces, hyphens, numbers, or punctuation)."
    )


class InvalidThemeError(ValueError):
    """Raised when a user-supplied theme fails format validation."""


def validate_theme(theme: str | None) -> str:
    """Return a normalised theme string or raise InvalidThemeError.

    Accepts only a single word: letters A-Z/a-z, no spaces or hyphens,
    length bounded by crosswords_theme_{min,max}_len (inclusive).
    """
    cfg = ConfigManager()
    if theme is None:
        raise InvalidThemeError("Theme is required.")
    candidate = theme.strip()
    if not candidate:
        raise InvalidThemeError("Theme is required.")
    if not candidate.isalpha():
        raise InvalidThemeError("Theme must contain letters only — no spaces, hyphens, digits, or punctuation.")
    if len(candidate) < cfg.crosswords_theme_min_len or len(candidate) > cfg.crosswords_theme_max_len:
        raise InvalidThemeError(
            f"Theme must be between {cfg.crosswords_theme_min_len} and "
            f"{cfg.crosswords_theme_max_len} letters long."
        )
    return candidate.lower()

# Deterministic debug fixtures: one 5-word set per (theme, difficulty).
# Chosen so the greedy placer can fit all five into a small grid.
DEBUG_SETS: Dict[Tuple[str, int], List[WordClue]] = {
    # ---- cats ----
    ("cats", 1): [
        ("PAW", "Foot of a cat"),
        ("MEOW", "Classic cat sound"),
        ("PURR", "Happy cat rumble"),
        ("TAIL", "Swishes when annoyed"),
        ("WHISKER", "Sensory face hair"),
    ],
    ("cats", 2): [
        ("KITTEN", "Baby cat"),
        ("CATNIP", "Herb cats go wild for"),
        ("LITTER", "Box filler, or a group of kittens"),
        ("TABBY", "Striped coat pattern"),
        ("FELINE", "Cat family adjective"),
    ],
    ("cats", 3): [
        ("SCRATCH", "What the post is for"),
        ("HAIRBALL", "Unpleasant cat offering"),
        ("CALICO", "Tri-color coat"),
        ("CATLOAF", "Cat tucked into a bread shape"),
        ("ZOOMIES", "Sudden sprint around the house"),
    ],
    ("cats", 4): [
        ("SPHYNX", "Hairless breed"),
        ("RAGDOLL", "Floppy, affectionate breed"),
        ("BOBTAIL", "Short-tailed breed type"),
        ("CHIMERA", "Cat with two-toned split face"),
        ("CROUCH", "Low stance before a pounce"),
    ],
    ("cats", 5): [
        ("MAINECOON", "Large tufted-ear breed"),
        ("NEUTERED", "Fixed, in pet terms"),
        ("CREPUSCULAR", "Active at dawn and dusk"),
        ("VIBRISSAE", "Anatomical name for whiskers"),
        ("TAPETUM", "Reflective layer behind the retina"),
    ],

    # ---- careers ----
    ("careers", 1): [
        ("NURSE", "Hospital caregiver"),
        ("CHEF", "Runs the kitchen"),
        ("PILOT", "Flies the plane"),
        ("TEACHER", "Explains things at the front of class"),
        ("DOCTOR", "Prescribes the meds"),
    ],
    ("careers", 2): [
        ("CODER", "Writes software for a living"),
        ("DESIGNER", "Figma professional"),
        ("BARISTA", "Makes your flat white"),
        ("EDITOR", "Polishes the draft"),
        ("TRADER", "Buys and sells on the market"),
    ],
    ("careers", 3): [
        ("FREELANCER", "Self-employed for-hire worker"),
        ("RECRUITER", "LinkedIn cold-messager"),
        ("PARAMEDIC", "Ambulance responder"),
        ("PRODUCER", "Runs the session or the show"),
        ("ANALYST", "Makes the deck with the charts"),
    ],
    ("careers", 4): [
        ("ACTUARY", "Calculates insurance risk"),
        ("ARCHITECT", "Designs the building"),
        ("PARALEGAL", "Law firm support role"),
        ("COPYWRITER", "Writes the ad copy"),
        ("CONSULTANT", "Billable-hour problem solver"),
    ],
    ("careers", 5): [
        ("CRYPTOGRAPHER", "Codes and ciphers professional"),
        ("EPIDEMIOLOGIST", "Tracks disease outbreaks"),
        ("ETHNOGRAPHER", "Studies cultures in the field"),
        ("NEUROSCIENTIST", "Brain researcher"),
        ("ORTHODONTIST", "Braces specialist"),
    ],

    # ---- music instruments ----
    ("music", 1): [
        ("DRUM", "You hit it with sticks"),
        ("PIANO", "88 keys"),
        ("GUITAR", "Six strings, often electric"),
        ("FLUTE", "Blown sideways"),
        ("BASS", "Low-end string instrument"),
    ],
    ("music", 2): [
        ("UKULELE", "Tiny four-string"),
        ("TROMBONE", "Brass with a slide"),
        ("CELLO", "Sits between your knees"),
        ("VIOLIN", "Held under the chin"),
        ("TRUMPET", "Three-valve brass"),
    ],
    ("music", 3): [
        ("SYNTH", "Electronic keyboard, for short"),
        ("BANJO", "Bluegrass staple"),
        ("SAXOPHONE", "Curvy reed instrument"),
        ("CAJON", "Box drum you sit on"),
        ("HARMONICA", "Pocket-sized blues reed"),
    ],
    ("music", 4): [
        ("MANDOLIN", "Pear-shaped string instrument"),
        ("DIDGERIDOO", "Long Australian wind instrument"),
        ("THEREMIN", "Played without being touched"),
        ("MARIMBA", "Wooden bars struck with mallets"),
        ("ACCORDION", "Bellows-driven keyboard"),
    ],
    ("music", 5): [
        ("HARPSICHORD", "Plucked-string keyboard predecessor to piano"),
        ("VIBRAPHONE", "Metal-bar mallet instrument with motor"),
        ("CONTRABASSOON", "Lowest of the double reeds"),
        ("HURDYGURDY", "Crank-wheeled string instrument"),
        ("GLOCKENSPIEL", "Small metal-bar percussion"),
    ],

    # ---- sports ----
    ("sports", 1): [
        ("GOAL", "Soccer scoring target"),
        ("BALL", "Round play object"),
        ("SWIM", "Pool activity"),
        ("TENNIS", "Racket sport with a net"),
        ("RUN", "Basic track action"),
    ],
    ("sports", 2): [
        ("SOCCER", "Football, US-style"),
        ("CRICKET", "Bat-and-ball sport played in whites"),
        ("HOCKEY", "Sticks on ice or turf"),
        ("BOXING", "Gloved combat sport"),
        ("SURFING", "Ride the wave"),
    ],
    ("sports", 3): [
        ("PENALTY", "Free shot awarded after a foul"),
        ("REBOUND", "Grab after a missed shot"),
        ("SLALOM", "Zigzag ski race"),
        ("VOLLEY", "Hit before the ball bounces"),
        ("DRIBBLE", "Basketball bouncing move"),
    ],
    ("sports", 4): [
        ("OFFSIDE", "Soccer positioning violation"),
        ("FREEKICK", "Unopposed dead-ball set piece"),
        ("BACKHAND", "Tennis shot across the body"),
        ("DEADLIFT", "Barbell off the floor lift"),
        ("KITESURF", "Board ride towed by a wing"),
    ],
    ("sports", 5): [
        ("PELOTON", "Main group in a cycling race"),
        ("CALISTHENICS", "Bodyweight workout discipline"),
        ("PARAGLIDING", "Foot-launched soaring"),
        ("PENTATHLON", "Five-event athletics contest"),
        ("STEEPLECHASE", "Race with water jumps and hurdles"),
    ],

    # ---- ai ----
    ("ai", 1): [
        ("BOT", "Automated chat helper"),
        ("CHAT", "Conversation window"),
        ("MODEL", "Trained AI system"),
        ("DATA", "Training fuel"),
        ("PROMPT", "What you type to the AI"),
    ],
    ("ai", 2): [
        ("TOKEN", "Unit of model input"),
        ("AGENT", "AI that takes actions"),
        ("CLAUDE", "Anthropic's assistant"),
        ("GPU", "Parallel chip used for training"),
        ("EMBED", "Turn text into vectors, for short"),
    ],
    ("ai", 3): [
        ("CONTEXT", "Window of recent tokens"),
        ("INFERENCE", "Running the trained model"),
        ("FINETUNE", "Further-train on a specific task"),
        ("DATASET", "Labeled pile of examples"),
        ("HALLUCINATE", "Confidently make things up"),
    ],
    ("ai", 4): [
        ("TRANSFORMER", "Attention-based model architecture"),
        ("RAG", "Retrieval-augmented generation, for short"),
        ("QUANTIZE", "Shrink weights to lower precision"),
        ("ENCODER", "Half of a seq2seq model"),
        ("DIFFUSION", "Denoising image-generation approach"),
    ],
    ("ai", 5): [
        ("BACKPROPAGATION", "Gradient training algorithm"),
        ("ATTENTION", "Mechanism that weighs token relevance"),
        ("REINFORCEMENT", "Learning from rewards"),
        ("TOKENIZER", "Chops text into pieces"),
        ("PERPLEXITY", "Language-model evaluation metric"),
    ],
}

# General-purpose fallback pool (used when the primary source returns
# nothing usable).
FALLBACK_POOL: List[WordClue] = [
    ("PYTHON", "Snake that also compiles to bytecode"),
    ("CLOUD", "Fluffy sky object, or remote server"),
    ("HONEY", "Sweet bee product"),
    ("OCEAN", "Vast saltwater body"),
    ("FOREST", "Dense collection of trees"),
    ("EMBER", "Glowing remnant of a fire"),
    ("HARBOR", "Sheltered spot for ships"),
    ("LANTERN", "Portable light in a case"),
    ("MEADOW", "Grassy open field"),
    ("PEBBLE", "Small smooth stone"),
    ("RIBBON", "Long strip of fabric"),
    ("SUMMIT", "Highest point of a mountain"),
    ("VALLEY", "Low area between hills"),
    ("BREEZE", "Gentle wind"),
    ("COMET", "Icy visitor with a glowing tail"),
]


def clamp_difficulty(difficulty: int) -> int:
    cfg = ConfigManager()
    try:
        d = int(difficulty)
    except (TypeError, ValueError):
        return cfg.crosswords_default_difficulty
    return max(cfg.crosswords_difficulty_min, min(cfg.crosswords_difficulty_max, d))
