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

### Planned Phoneme Dictionary Feature
Currently in the investigation / pre-planning stage (as of 2026-11-10),
the app will gain a feature enabling the creation of phoneme substitution
dictionary.

Instead of using inline IPA phoneme substitutions, the new feature will 
enable adding {word, phoneme} pairs to a dictionary so that they will
be substituted globally. This will be most convenient for words or phrases
that are used frequently, such as the aforementioned "Delaware" and "Association".
Other mispronounced words include "our" and "Hertz"

