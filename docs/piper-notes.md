# Piper Text to Speech Notes

Piper sometimes mis-pronounces words, and the pronunciation 
sometimes varies quite a bit among voices.

Piper does not have a way to set up a dictionary of word pronunciations,
or specific phonemes, so word substitutions and other workarounds directly
in the announcement text may be necessary to get acceptable pronunciation.

The following substitutions and other workarounds have been found
to work in many situations.

## General Workarounds
 - If you find that the speech is a bit stilted, or that there are unnatural pauses
between words, try concatenation words with dashes, e.g. "P-L-tone"

 - Also for more fluid speech, it sometimes helps to concatenate words with commas,
 e.g "four,four,two,point,six,five,zero,mega-hertz" 

## Plain-Text Substitutions

| Word         |    Substitution   |
| ---          | ---               |
|Delaware      |  Del-aware        | 
|Association   |  Asso-see-ation   |
|              |                   |

## Inline IPA (International Phonetic Alphabet) Phoneme Substitutions
The Piper TTS engine supports IPA phoneme notation inline in the input text, with the 
phoneme string surrounded by '[[' and ']]'.

For instance,
```text
"This is the [[dˈɛlɐwˌɛ‍ɹ]] Valley Radio [[ɐsˌo‍ʊsɪˈe‍ɪʃən]]"
```

For a starting point, these phonemes can be generated at the command-line
with `espeak-ng -v en-us --ipa=3 {plaintext}`, for example,

```bash
espeak-ng -v en-us --ipa=3 Delaware
```
produces

```bash
dˈɛlɐwˌɛ‍ɹ
```

This frequently produces better pronunciation for words mispronounced 
by the default Piper internal phoneme conversion. "Delaware" and "Association"
are both cases where this is true.

## Phoneme dictionary

The Pronunciations screen stores a word or phrase spelling with either an IPA
string or alternate plain text, not both. While a spoken announcement has
**Use the phoneme dictionary** on (the default, including announcements saved
before this existed), those spellings are replaced when the announcement is
synthesized. IPA is spoken as phonemes. Alternate text is spoken as ordinary
words. The stored announcement text is unchanged.
The dictionary file is `phonemes.json` in the data directory. It is not part of
the git repository. Its JSON `version` is 1.

Matching ignores case and only replaces whole words and phrases. A longer phrase
wins over a shorter one. "our" does not change "hour". "Delaware's" does not use
an entry for "Delaware"; add "Delaware's" if that possessive needs an entry.

`{{phrase}}` in the announcement is spoken as written and skips the dictionary.
The braces are not spoken. `[[ipa]]` is still manual phoneme input and is not
rewritten. `[pause:1s]` is still a pause.

**IPA for this spelling** on the Pronunciations screen uses the espeak-ng
library shipped with Piper, with the same phoneme mode as
`espeak-ng -v en-us --ipa=3` (including tie marks and stress). A word looked up
by itself is stressed the way that command stresses it, so `our` alone is
`ˈaʊɚ` rather than the unstressed form used inside a sentence. Pick a voice on
that screen and speak the spelling, the IPA, or the alternate text before saving the row.
Tie marks are easy to miss because the joiner has no width. The saved IPA keeps them. They are removed from `[[ ]]` blocks when text is prepared for Piper, which has no phoneme id for U+200D and would only skip the character.

