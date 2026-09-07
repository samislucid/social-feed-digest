"""Social feed digest worker.

Pipeline: collect (xAI Live Search, Reddit RSS/PRAW, LinkedIn watched inbox)
-> rank -> draft (claude -p with template fallback) -> render -> deliver
(email + private token-protected page).
"""

__version__ = "0.1.0"
