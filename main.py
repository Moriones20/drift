"""Drift — autonomous forex trend-following bot.

Entry point. Initialises all subsystems and runs the main 4-hour loop.
Implementation is added in later steps (see ROADMAP.md Phase 1).
"""

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).info("Drift bot starting...")
