# MinerU enhancement worker

This isolated Python 3.12 worker processes only pages selected by the OpenClass quality router. It uses MinerU's local `pipeline` backend and never configures a cloud API.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x worker.py
export OPENCLASS_MINERU_COMMAND="$PWD/worker.py"
export OPENCLASS_MINERU_VERSION="3.4.4"
```

MinerU execution is guarded by a process-wide concurrency limit of one.
