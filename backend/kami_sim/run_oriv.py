"""Oriv CLI runner.

Usage: python -m kami_sim.run_oriv --ticks 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Ensure the backend directory is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from .config import config
from .factstore.models import init_db
from .llm.budget import budget
from .scheduler.tick_scheduler import TickScheduler
from .oriv_world import build_oriv_world

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run_oriv(num_ticks: int = 10, output_file: str = "oriv_output.json"):
    """Run the Oriv simulation."""
    logger.info("=" * 60)
    logger.info("KAMI SIMULATION — ORIV")
    logger.info("=" * 60)

    # Initialize database (in-memory for slice) # For now we use the same as slice test logic
    db_url = "sqlite:///./kami_oriv.db"
    engine, session_factory = init_db(db_url)
    session = session_factory()

    # Build the test world
    logger.info("Building Oriv world...")
    spatial_graph = build_oriv_world(session)
    session.close()

    # Verify graph connectivity
    assert spatial_graph.is_connected(), "Spatial graph is not connected!"
    logger.info(f"Spatial graph: {len(spatial_graph.all_kami_ids())} nodes, connected=True")

    # Create scheduler and run
    scheduler = TickScheduler(
        session_factory=session_factory,
        spatial_graph=spatial_graph,
    )

    logger.info(f"Running {num_ticks} ticks...")
    tick_log = await scheduler.run(num_ticks=num_ticks)

    # Output results
    output = {
        "config": {
            "num_ticks": num_ticks,
            "cheap_model": config.cheap_model_name,
            "strong_model": config.strong_model_name,
        },
        "budget": budget.get_summary(),
        "ticks": tick_log,
    }

    output_path = Path(output_file)
    output_path.write_text(json.dumps(output, indent=2, default=str))
    logger.info(f"Output written to {output_path}")

    # Print summary
    logger.info("=" * 60)
    logger.info("SIMULATION COMPLETE")
    logger.info("=" * 60)
    summary = budget.get_summary()
    logger.info(f"Total cost: ${summary['total_cost_usd']:.4f}")
    logger.info(f"Total LLM calls: {summary['total_calls']}")
    logger.info(f"Total tokens: {summary['total_input_tokens']} in / {summary['total_output_tokens']} out")
    logger.info(f"Cost by component: {summary['by_component']}")

    # Validation: count events
    total_events = sum(len(t.get("events", [])) for t in tick_log)
    error_ticks = sum(1 for t in tick_log if "error" in t)
    logger.info(f"Total events emitted: {total_events}")
    logger.info(f"Error ticks: {error_ticks}")

    return output


def main():
    parser = argparse.ArgumentParser(description="Run Kami Simulation Oriv")
    parser.add_argument("--ticks", type=int, default=10, help="Number of ticks to simulate")
    parser.add_argument("--output", type=str, default="oriv_output.json", help="Output JSON file")
    args = parser.parse_args()

    asyncio.run(run_oriv(num_ticks=args.ticks, output_file=args.output))


if __name__ == "__main__":
    main()
