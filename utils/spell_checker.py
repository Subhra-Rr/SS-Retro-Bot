"""
Music Search Spell Correction Utility.
Detects and corrects common typos in artist names, retro music terms, and song keywords.
Example: 'Arijt Singh' -> 'Arijit Singh', 'Kishor Kumar' -> 'Kishore Kumar'.
"""

from __future__ import annotations

import difflib
import re
from typing import Optional, Tuple

# Comprehensive dictionary of well-known music artists and terms
KNOWN_ARTISTS_AND_TERMS = [
    # Top Hindi / Bollywood & Retro Artists
    "Arijit Singh",
    "Kishore Kumar",
    "Lata Mangeshkar",
    "Mohammed Rafi",
    "Mukesh",
    "Asha Bhosle",
    "R.D. Burman",
    "S.D. Burman",
    "Sonu Nigam",
    "Kumar Sanu",
    "Alka Yagnik",
    "Udit Narayan",
    "Shreya Ghoshal",
    "Sunidhi Chauhan",
    "Atif Aslam",
    "Armaan Malik",
    "Jubin Nautiyal",
    "Badshah",
    "Yo Yo Honey Singh",
    "Diljit Dosanjh",
    "Sidhu Moosewala",
    "AP Dhillon",
    "Neha Kakkar",
    "KK",
    "Shaan",
    "Mohit Chauhan",
    "Ankit Tiwari",
    "Darshan Raval",
    "B Praak",
    "Himesh Reshammiya",
    "Jagjit Singh",
    "Pankaj Udhas",
    "Nusrat Fateh Ali Khan",
    "Rahat Fateh Ali Khan",
    "A.R. Rahman",
    "Pritam",
    "Vishal-Shekhar",
    "Sachin-Jigar",
    "Mithoon",
    "Amit Trivedi",
    # Regional (Odia, Bengali, Punjabi, South)
    "Akshaya Mohanty",
    "Bhikari Bal",
    "Humane Sagar",
    "Asima Panda",
    "Babushaan",
    "Anubhav Mohanty",
    "Manna Dey",
    "Hemant Kumar",
    "K.J. Yesudas",
    "S.P. Balasubrahmanyam",
    "Sid Sriram",
    "Anirudh Ravichander",
    # International & Pop Artists
    "Michael Jackson",
    "Freddie Mercury",
    "Queen",
    "The Beatles",
    "Pink Floyd",
    "Led Zeppelin",
    "Eminem",
    "Taylor Swift",
    "Ed Sheeran",
    "Drake",
    "Justin Bieber",
    "The Weeknd",
    "Billie Eilish",
    "Coldplay",
    "Bruno Mars",
    "Adele",
    "Dua Lipa",
    "Post Malone",
    "Imagine Dragons",
    "Linkin Park",
    "Alan Walker",
    # Popular Song Phrases
    "Tum Hi Ho",
    "Kal Ho Naa Ho",
    "Kesariya",
    "Tere Hawaale",
    "Apna Bana Le",
    "Chaleya",
    "Raataan Lambiyan",
    "Dil Diyan Gallan",
    "Shayad",
    "Pehli Nazar Mein",
]


def correct_spelling(query: str) -> Tuple[str, Optional[str]]:
    """
    Check if the search query contains typographical errors in artist or song names.
    Returns: (corrected_query: str, original_query_or_none_if_unchanged: Optional[str])
    """
    cleaned = query.strip()
    if not cleaned:
        return query, None

    corrected = cleaned

    # Check against known multi-word artists & terms
    for term in KNOWN_ARTISTS_AND_TERMS:
        term_words = term.split()
        term_len = len(term_words)

        # Check full query match or subphrase match
        pattern = r"\b" + r"\s+".join([re.escape(w) for w in term_words]) + r"\b"
        if re.search(pattern, cleaned, re.IGNORECASE):
            continue  # Already exact match

        # Check n-gram windows
        query_words = cleaned.split()
        if len(query_words) >= term_len:
            for i in range(len(query_words) - term_len + 1):
                window = " ".join(query_words[i : i + term_len])
                # Check similarity ratio
                ratio = difflib.SequenceMatcher(None, window.lower(), term.lower()).ratio()
                if 0.75 <= ratio < 1.0:
                    # Found a close match / typo!
                    old_sub = window
                    query_words[i : i + term_len] = term.split()
                    corrected = " ".join(query_words)
                    return corrected, cleaned

    # Also check individual words if query is 1 or 2 words
    words = cleaned.split()
    changed = False
    new_words = []
    for w in words:
        if len(w) > 4:
            matches = difflib.get_close_matches(
                w.lower(), [t.lower() for t in KNOWN_ARTISTS_AND_TERMS if " " not in t], n=1, cutoff=0.8
            )
            if matches and matches[0] != w.lower():
                # Find matching capitalized term
                cap = next((t for t in KNOWN_ARTISTS_AND_TERMS if t.lower() == matches[0]), matches[0].capitalize())
                new_words.append(cap)
                changed = True
                continue
        new_words.append(w)

    if changed:
        return " ".join(new_words), cleaned

    return cleaned, None
