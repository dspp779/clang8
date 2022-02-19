"""Main file to combine cLang-8 targets with the original Lang-8 sources.

Before running this, download the Lang-8 raw corpus from:

https://docs.google.com/forms/d/17gZZsC_rnaACMXmPiab3kjqBEtRHPMz0UG9Dk-x_F0k/viewform?edit_requested=true

and provide the download directory path via the `lang8_dir` flag.
"""

from collections import defaultdict
from typing import Iterable, Iterator, List, Mapping, Sequence, Tuple
import json
import os

from tqdm import tqdm
import spacy

from absl import app
from absl import flags


FLAGS = flags.FLAGS

flags.DEFINE_string(
    'lang8_dir', '',
    'Path to the directory containing the Lang-8 raw corpus, specifically the '
    'following version of it: lang-8-20111007-L1-v2.dat')
flags.DEFINE_string(
    'clang8_dir', './targets',
    'Path to the directory containing the cLang-8 files downloaded from '
    'GitHub.')
flags.DEFINE_string(
    'output_dir', './output_data',
    'Path to the directory where the output files are written.')
flags.DEFINE_bool(
    'tokenize_text', True,
    'Whether to tokenize sources and targets using spaCy.')
flags.DEFINE_list(
    'languages', 'ru,en,de',
    'Comma-separated list of languages for which to generate cLang-8.')


def _yield_lang8_raw_dicts(lang8_raw_dir: str):
  """Yields JSON rows from the Lang-8 raw corpus.

  Format of the rows is documented at:
  https://sites.google.com/site/naistlang8corpora/home/readme-raw

  Args:
    lang8_raw_dir: Directory containing the Lang-8 raw corpus, specifically the
      following version of it: lang-8-20111007-L1-v2.dat
  """
  path = os.path.join(lang8_raw_dir, 'lang-8-20111007-L1-v2.dat')
  num_rows = 0
  with open(path) as f:
    for line in f:
      try:
        row = json.loads(line)
        yield row
        num_rows += 1
      except json.decoder.JSONDecodeError:
        pass
  print(f'{num_rows} Lang-8 raw documents read.')


def _read_clang8_targets(
    path: str) -> Tuple[Mapping[Tuple[str, str], List[Tuple[str, str]]], int]:
  """Reads cLang-8 target generated by gT5.

  Args:
    path: Path to a language-specific cLang-8 targets.

  Returns:
    (journal_id, sentence_id) pair referring to Lang-8 raw IDs mapped to
      (sentence_number, target) where sentence_number is the learner sentence
      index.
  """
  ids_2_targets = defaultdict(list)
  with open(path) as f:
    for line in f.read().splitlines():
      journal_id, sentence_id, sentence_number, _, target = line.split('\t')
      ids_2_targets[int(journal_id), int(sentence_id)].append((int(sentence_number), target))
  num_targets = sum(len(targets) for targets in ids_2_targets.values())
  print(f'{num_targets} cLang-8 targets read.')
  return ids_2_targets, num_targets


def _yield_clang8_source_target_pairs(
    clang8_path: str, lang8_raw_dir: str) -> Iterator[Tuple[str, str]]:
  """Yields cLang-8 source-target pairs.

  The pairs are obtained by combining the cLang-8 target file and the original
  Lang-8 raw corpus.

  Args:
    clang8_path: Path to a language-specific cLang-8 targets.
    lang8_raw_dir: Directory containing the Lang-8 raw corpus, specifically the
      following version of it: lang-8-20111007-L1-v2.dat
  """
  ids_2_targets, num_targets = _read_clang8_targets(clang8_path)
  with tqdm(total=num_targets) as progress_bar:
    for journal_id, sentence_id, *_, sources, _ in _yield_lang8_raw_dicts(lang8_raw_dir):
      lang8_raw_ids = (int(journal_id), int(sentence_id))
      for sentence_number, target in ids_2_targets.get(lang8_raw_ids, ()):
        yield sources[sentence_number], target
        progress_bar.update(1)
    print(f'{progress_bar.n} sources mapped to cLang-8 targets.')


def _tokenize(pairs: Iterable[Tuple[str, str]],
              nlp: spacy.Language,
              batch_size: int = 1000) -> Iterator[Tuple[str, str]]:
  """Yields the input source-target pairs after tokenizing them.

  NB: This function loads all source-target pairs to memory at once.

  Args:
    pairs: Untokenized (source, target) pairs.
    nlp: SpaCy pipeline.
    batch_size: Batch size used with `nlp.pipe`.

  Yields:
    (tokenized source, tokenized target) pairs.
  """
  # Convert iterator to list to be able to separate sources and targets so that
  # we can use `nlp.pipe` with batching for increased throughput.
  pairs = list(pairs)
  print('Tokenizing...')
  source_docs = nlp.pipe([pair[0] for pair in pairs], batch_size=batch_size)
  target_docs = nlp.pipe([pair[1] for pair in pairs], batch_size=batch_size)
  with tqdm(total=len(pairs)) as progress_bar:
    for source, target in zip(source_docs, target_docs):
      source_tokenized = ' '.join([token.text for token in source])
      target_tokenized = ' '.join([token.text for token in target])
      yield source_tokenized, target_tokenized
      progress_bar.update(1)


def _clean_spaces(text):
  """Removes tabs and newlines for saving as TSV."""
  return text.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')


def _write_source_target_pairs_to_tsv(pairs: Iterable[Tuple[str, str]],
                                      output_path: str) -> None:
  """Saves source-target pairs separated with a tab to a file."""
  with open(output_path, 'w') as f:
    for source, target in pairs:
      source = _clean_spaces(source)
      target = _clean_spaces(target)
      f.write(f'{source}\t{target}\n')
  print(f'Wrote the source-target pairs to:\n{output_path}')


def _prepare_clang8(language: str, clang8_targets_dir: str, lang8_dir: str,
                    output_dir: str, tokenize_text: str) -> None:
  """Prepares the cLang-8 dataset for a single language."""
  # Load tokenizer.
  if language == 'en':
    model_path = 'en_core_web_sm'
  elif language == 'de':
    model_path = 'de_core_news_sm'
  elif language == 'ru':
    model_path = 'ru_core_news_sm'
  else:
    raise ValueError(f'Unsupported language: {language}')
  disabled_components = ['lemmatizer', 'parser', 'tagger', 'ner']

  clang8_targets_path = os.path.join(clang8_targets_dir,
                                     f'clang8_{language}.detokenized.tsv')
  source_target_pairs = _yield_clang8_source_target_pairs(clang8_targets_path,
                                                          lang8_dir)
  tokenization_label = ''
  if tokenize_text:
    nlp = spacy.load(model_path, disable=disabled_components)
    tokenization_label = '.spacy_tokenized'
    source_target_pairs = _tokenize(source_target_pairs, nlp)
  output_path = os.path.join(
      output_dir, f'clang8_source_target_{language}{tokenization_label}.tsv')
  _write_source_target_pairs_to_tsv(source_target_pairs, output_path)


def main(argv: Sequence[str]) -> None:
  if len(argv) > 1:
    raise app.UsageError('Too many command-line arguments.')

  for language in FLAGS.languages:
    print(f'\n{language}')
    _prepare_clang8(language, FLAGS.clang8_dir, FLAGS.lang8_dir,
                    FLAGS.output_dir, FLAGS.tokenize_text)


if __name__ == '__main__':
  app.run(main)
