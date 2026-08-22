"""The brain — durable subjects (threads), and the read path that consults them.

The scheduled automations write; this package is what reads. See threads.py for why a state
line rather than an ever-growing pile, and recall.py for why lexical rather than embedded.
"""

from .recall import Recall, recall
from .threads import Thread, brain_dir, load, load_all, save, slugify

__all__ = [
    "Recall",
    "Thread",
    "brain_dir",
    "load",
    "load_all",
    "recall",
    "save",
    "slugify",
]
