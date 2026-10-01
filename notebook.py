import json
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4


class Notebook:
    """Full session records and their notebook representation."""

    def __init__(self):
        self.cells = []

    def start_cell(self, number: int, summary: str, code: str) -> None:
        self.cells.append(
            {
                "cell_type": "code",
                "id": uuid4().hex,
                "metadata": {"fibona": {"number": number, "summary": summary}},
                "source": code,
                "execution_count": None,
                "outputs": [],
            }
        )

    def output(self, channel: str, text: str) -> None:
        if not self.cells or not text:
            return
        outputs = self.cells[-1]["outputs"]
        if outputs and outputs[-1]["output_type"] == "stream" and outputs[-1]["name"] == channel:
            outputs[-1]["text"] += text
        else:
            outputs.append({"output_type": "stream", "name": channel, "text": text})

    def finish_cell(self, error: str | None) -> None:
        cell = self.cells[-1]
        cell["execution_count"] = len(self.cells)
        if error:
            lines = error.rstrip().splitlines()
            name, separator, value = lines[-1].partition(":")
            cell["outputs"].append(
                {
                    "output_type": "error",
                    "ename": name if name.isidentifier() else "CellError",
                    "evalue": value.lstrip() if separator else lines[-1],
                    "traceback": lines,
                }
            )

    def save(self, path: Path) -> None:
        """Write completely before replacing the saved notebook."""
        if not self.cells:
            return
        document = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {
                "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                "language_info": {"name": "python", "version": sys.version.split()[0]},
            },
            "cells": self.cells,
        }
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete_on_close=False) as file:
            json.dump(document, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.close()
            Path(file.name).replace(path)
