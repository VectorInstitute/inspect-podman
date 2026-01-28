"""Helpers for Inspect Evals wrappers."""

from __future__ import annotations

from inspect_ai import Task
from inspect_ai.dataset import Dataset, MemoryDataset, Sample
from inspect_ai.util import SandboxEnvironmentSpec


class _PodmanDataset(Dataset):
    """Dataset wrapper that rewrites docker sandboxes to podman on access."""

    def __init__(self, inner: Dataset) -> None:
        self._inner = inner

    @property
    def name(self) -> str | None:
        return self._inner.name

    @property
    def location(self) -> str | None:
        return self._inner.location

    @property
    def shuffled(self) -> bool:
        return self._inner.shuffled

    def __len__(self) -> int:
        return len(self._inner)

    def __getitem__(self, index):
        item = self._inner[index]
        if isinstance(item, Sample):
            _convert_sample(item)
            return item
        if isinstance(item, Dataset):
            return _PodmanDataset(item)
        return item

    def sort(self, reverse: bool = False, key=None) -> None:
        if key is None:
            self._inner.sort(reverse=reverse)
        else:
            self._inner.sort(reverse=reverse, key=key)

    def filter(self, predicate, name: str | None = None):
        return _PodmanDataset(self._inner.filter(predicate, name=name))

    def shuffle(self, seed: int | None = None) -> None:
        self._inner.shuffle(seed)

    def shuffle_choices(self, seed: int | None = None) -> None:
        self._inner.shuffle_choices(seed)


def _convert_sample(sample: Sample) -> None:
    if sample.sandbox and sample.sandbox.type == "docker":
        sample.sandbox = SandboxEnvironmentSpec("podman", sample.sandbox.config)


def as_podman(task: Task) -> Task:
    """Rewrite docker sandbox specs to podman."""
    if task.sandbox and task.sandbox.type == "docker":
        task.sandbox = SandboxEnvironmentSpec("podman", task.sandbox.config)

    dataset = task.dataset
    if dataset is None:
        return task

    if isinstance(dataset, list):
        for sample in dataset:
            if isinstance(sample, Sample):
                _convert_sample(sample)
        return task

    if isinstance(dataset, MemoryDataset):
        for sample in dataset.samples:
            _convert_sample(sample)
        return task

    if isinstance(dataset, Dataset):
        task.dataset = _PodmanDataset(dataset)

    return task
