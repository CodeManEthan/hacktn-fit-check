"""One vocabulary for languages: English names, everywhere past the edges.

Device documents give languages as ISO codes ("en"), clinic profiles and the
import table give names ("English"), and a set intersection of the two is
empty. Every list of languages passes through `normalise_language` where it
enters the app, so no check ever compares a code with a name.
"""

from __future__ import annotations

from typing import Iterable

# ISO 639-1, the full table: code -> English name. Where the standard gives
# several names the first is kept, except "Haitian" (written "Haitian
# Creole", the name the clinic profiles use) and "Chichewa" (over "Nyanja").
ISO_639_1: dict[str, str] = {
    "aa": "Afar", "ab": "Abkhazian", "ae": "Avestan", "af": "Afrikaans",
    "ak": "Akan", "am": "Amharic", "an": "Aragonese", "ar": "Arabic",
    "as": "Assamese", "av": "Avaric", "ay": "Aymara", "az": "Azerbaijani",
    "ba": "Bashkir", "be": "Belarusian", "bg": "Bulgarian", "bi": "Bislama",
    "bm": "Bambara", "bn": "Bengali", "bo": "Tibetan", "br": "Breton",
    "bs": "Bosnian", "ca": "Catalan", "ce": "Chechen", "ch": "Chamorro",
    "co": "Corsican", "cr": "Cree", "cs": "Czech", "cu": "Church Slavic",
    "cv": "Chuvash", "cy": "Welsh", "da": "Danish", "de": "German",
    "dv": "Divehi", "dz": "Dzongkha", "ee": "Ewe", "el": "Greek",
    "en": "English", "eo": "Esperanto", "es": "Spanish", "et": "Estonian",
    "eu": "Basque", "fa": "Persian", "ff": "Fulah", "fi": "Finnish",
    "fj": "Fijian", "fo": "Faroese", "fr": "French", "fy": "Western Frisian",
    "ga": "Irish", "gd": "Scottish Gaelic", "gl": "Galician", "gn": "Guarani",
    "gu": "Gujarati", "gv": "Manx", "ha": "Hausa", "he": "Hebrew",
    "hi": "Hindi", "ho": "Hiri Motu", "hr": "Croatian", "ht": "Haitian Creole",
    "hu": "Hungarian", "hy": "Armenian", "hz": "Herero", "ia": "Interlingua",
    "id": "Indonesian", "ie": "Interlingue", "ig": "Igbo", "ii": "Sichuan Yi",
    "ik": "Inupiaq", "io": "Ido", "is": "Icelandic", "it": "Italian",
    "iu": "Inuktitut", "ja": "Japanese", "jv": "Javanese", "ka": "Georgian",
    "kg": "Kongo", "ki": "Kikuyu", "kj": "Kuanyama", "kk": "Kazakh",
    "kl": "Kalaallisut", "km": "Khmer", "kn": "Kannada", "ko": "Korean",
    "kr": "Kanuri", "ks": "Kashmiri", "ku": "Kurdish", "kv": "Komi",
    "kw": "Cornish", "ky": "Kyrgyz", "la": "Latin", "lb": "Luxembourgish",
    "lg": "Ganda", "li": "Limburgish", "ln": "Lingala", "lo": "Lao",
    "lt": "Lithuanian", "lu": "Luba-Katanga", "lv": "Latvian", "mg": "Malagasy",
    "mh": "Marshallese", "mi": "Maori", "mk": "Macedonian", "ml": "Malayalam",
    "mn": "Mongolian", "mr": "Marathi", "ms": "Malay", "mt": "Maltese",
    "my": "Burmese", "na": "Nauru", "nb": "Norwegian Bokmal", "nd": "North Ndebele",
    "ne": "Nepali", "ng": "Ndonga", "nl": "Dutch", "nn": "Norwegian Nynorsk",
    "no": "Norwegian", "nr": "South Ndebele", "nv": "Navajo", "ny": "Chichewa",
    "oc": "Occitan", "oj": "Ojibwa", "om": "Oromo", "or": "Oriya",
    "os": "Ossetian", "pa": "Punjabi", "pi": "Pali", "pl": "Polish",
    "ps": "Pashto", "pt": "Portuguese", "qu": "Quechua", "rm": "Romansh",
    "rn": "Rundi", "ro": "Romanian", "ru": "Russian", "rw": "Kinyarwanda",
    "sa": "Sanskrit", "sc": "Sardinian", "sd": "Sindhi", "se": "Northern Sami",
    "sg": "Sango", "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian",
    "sm": "Samoan", "sn": "Shona", "so": "Somali", "sq": "Albanian",
    "sr": "Serbian", "ss": "Swati", "st": "Southern Sotho", "su": "Sundanese",
    "sv": "Swedish", "sw": "Swahili", "ta": "Tamil", "te": "Telugu",
    "tg": "Tajik", "th": "Thai", "ti": "Tigrinya", "tk": "Turkmen",
    "tl": "Tagalog", "tn": "Tswana", "to": "Tonga", "tr": "Turkish",
    "ts": "Tsonga", "tt": "Tatar", "tw": "Twi", "ty": "Tahitian",
    "ug": "Uyghur", "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek",
    "ve": "Venda", "vi": "Vietnamese", "vo": "Volapuk", "wa": "Walloon",
    "wo": "Wolof", "xh": "Xhosa", "yi": "Yiddish", "yo": "Yoruba",
    "za": "Zhuang", "zh": "Chinese", "zu": "Zulu",
}

CHINESE_SIMPLIFIED = "Chinese (Simplified)"
CHINESE_TRADITIONAL = "Chinese (Traditional)"

# Tags whose script or region changes the name. Any other "xx-YY" tag is
# read as its base code, so "pt-BR" is Portuguese and "en-GB" is English.
_TAG_NAMES: dict[str, str] = {
    "zh-hans": CHINESE_SIMPLIFIED, "zh-cn": CHINESE_SIMPLIFIED,
    "zh-sg": CHINESE_SIMPLIFIED, "zh-hans-cn": CHINESE_SIMPLIFIED,
    "zh-hant": CHINESE_TRADITIONAL, "zh-tw": CHINESE_TRADITIONAL,
    "zh-hk": CHINESE_TRADITIONAL, "zh-hant-tw": CHINESE_TRADITIONAL,
}

# Other names and spellings a document or a clinic might use, lower case,
# including the endonyms manual covers print ("ESPANOL", "FRANCAIS") and the
# common ISO 639-2 codes.
_VARIANTS: dict[str, str] = {
    "simplified chinese": CHINESE_SIMPLIFIED, "chinese simplified": CHINESE_SIMPLIFIED,
    "zh (simplified)": CHINESE_SIMPLIFIED, "zh (traditional)": CHINESE_TRADITIONAL,
    "mandarin": "Chinese",
    "traditional chinese": CHINESE_TRADITIONAL, "chinese traditional": CHINESE_TRADITIONAL,
    "haitian": "Haitian Creole", "creole": "Haitian Creole", "kreyol": "Haitian Creole",
    "kreyòl": "Haitian Creole", "kreyol ayisyen": "Haitian Creole",
    "kreyòl ayisyen": "Haitian Creole", "haitian kreyol": "Haitian Creole",
    "castilian": "Spanish", "español": "Spanish", "espanol": "Spanish",
    "français": "French", "francais": "French", "deutsch": "German",
    "italiano": "Italian", "nederlands": "Dutch", "flemish": "Dutch",
    "türkçe": "Turkish", "turkce": "Turkish", "português": "Portuguese",
    "portugues": "Portuguese", "polski": "Polish", "bahasa melayu": "Malay",
    "bahasa indonesia": "Indonesian", "kiswahili": "Swahili", "farsi": "Persian",
    "nyanja": "Chichewa", "chewa": "Chichewa", "kirundi": "Rundi",
    "odia": "Oriya", "kirghiz": "Kyrgyz", "uighur": "Uyghur",
    "sinhalese": "Sinhala", "panjabi": "Punjabi", "pushto": "Pashto",
    "dhivehi": "Divehi", "maldivian": "Divehi", "moldovan": "Romanian",
    "gaelic": "Scottish Gaelic", "greenlandic": "Kalaallisut",
    "norwegian bokmål": "Norwegian Bokmal", "bokmål": "Norwegian Bokmal",
    "bokmal": "Norwegian Bokmal", "filipino": "Tagalog", "luganda": "Ganda",
    "isizulu": "Zulu", "isixhosa": "Xhosa", "sesotho": "Southern Sotho",
    "setswana": "Tswana", "afaan oromoo": "Oromo", "nepalese": "Nepali",
    "eng": "English", "fra": "French", "fre": "French", "spa": "Spanish",
    "deu": "German", "ger": "German", "ita": "Italian", "por": "Portuguese",
    "nld": "Dutch", "dut": "Dutch", "ara": "Arabic", "zho": "Chinese",
    "chi": "Chinese", "nep": "Nepali", "hat": "Haitian Creole",
    "swa": "Swahili", "rus": "Russian", "hin": "Hindi", "ben": "Bengali",
    "amh": "Amharic", "lug": "Ganda",
}

_NAME_BY_LOWER: dict[str, str] = {name.lower(): name for name in ISO_639_1.values()}
_NAME_BY_LOWER.update({n.lower(): n for n in _TAG_NAMES.values()})

# The code OCI Language takes for each name. Chinese scripts use the region
# tags OCI's translation service lists; every other name its ISO 639-1 code.
_CODE_BY_NAME: dict[str, str] = {name: code for code, name in ISO_639_1.items()}
_CODE_BY_NAME[CHINESE_SIMPLIFIED] = "zh-CN"
_CODE_BY_NAME[CHINESE_TRADITIONAL] = "zh-TW"


def normalise_language(text: str) -> str:
    """The English name for a code, a name in any case, or a common variant.

    "en", "English", "ENGLISH" -> "English"; "zh-Hans" -> "Chinese
    (Simplified)"; "ht" -> "Haitian Creole". A string matching nothing is
    title-cased and kept, so an unusual language is never dropped.
    """
    cleaned = " ".join(str(text or "").replace("_", "-").split())
    if not cleaned:
        return ""
    lowered = cleaned.lower()

    if lowered in _TAG_NAMES:
        return _TAG_NAMES[lowered]
    if lowered in ISO_639_1:
        return ISO_639_1[lowered]
    if lowered in _NAME_BY_LOWER:
        return _NAME_BY_LOWER[lowered]
    if lowered in _VARIANTS:
        return _VARIANTS[lowered]

    base, sep, _tail = lowered.partition("-")
    if sep and base in ISO_639_1:
        return ISO_639_1[base]
    return cleaned.title()


def normalise_languages(values: Iterable[str] | None) -> list[str]:
    """Every entry normalised, blanks dropped, duplicates removed, order kept."""
    seen: list[str] = []
    for value in values or ():
        name = normalise_language(value)
        if name and name not in seen:
            seen.append(name)
    return seen


def language_code(name: str) -> str | None:
    """The code OCI Language wants for a language, or None when it has none."""
    return _CODE_BY_NAME.get(normalise_language(name))
