# Contributing

Bug reports and focused pull requests are welcome. Please keep the core dependency-free and run:

```bash
python -m compileall -q src tests
PYTHONPATH=src python -m unittest discover -s tests -v
python tools/privacy_scan.py --root .
```

Changes to authority, conflict, completion, or recovery semantics require both a positive test and
a negative regression test. Never commit real conversation histories, credentials, private paths,
or proprietary project memories as fixtures.
