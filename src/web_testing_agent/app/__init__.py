"""The product layer: a web application wrapping the research system.

Imports flow one way only — this package calls into `web_testing_agent.*`, and nothing in
the research packages imports anything from here. Deleting this directory would leave the
research system exactly as it was.
"""

from .main import create_app

__all__ = ["create_app"]
