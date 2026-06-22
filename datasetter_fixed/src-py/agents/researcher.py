"""
agents/researcher.py

The Researcher activates when the Interpreter decides live information
is needed for accurate dataset generation.

Examples of when this fires:
  - "Generate Q&A about recent AI model releases"
  - "Dataset about current cryptocurrency prices and market caps"
  - "Examples using the latest Python 3.13 features"
  - "Customer support scenarios for [specific product] based on real issues"

Model: Gemini only (cloud) — it has the best native search grounding
and can retrieve, verify, and synthesise live web data in a single call.

Output: structured research context fed to Generator.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

import litellm

from core.litellm_router import LiteLLMRouter
from core.models import AgentID, AgentStatus, JobConfig
from utils.events import Emitter

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are the Researcher agent in Datasetter, an AI dataset generation pipeline.

Your job is to retrieve accurate, current, and relevant information from the internet
to inform dataset generation. You have access to Google Search through Gemini's grounding.

Given a list of research queries, you must:
1. Search for and retrieve current, factual information for each query
2. Synthesise the findings into a structured research brief
3. Include specific facts, figures, examples, and terminology the Generator needs
4. Note any conflicting information or areas of uncertainty
5. Flag information that may become outdated quickly

Output format:
## Research Brief

### [Query 1]
[Detailed findings with specific facts and examples]

### [Query 2]
[Detailed findings...]

## Key Facts for Dataset Generation
[Bullet list of the most important specific facts the Generator must use]

## Terminology and Conventions
[Domain-specific terms, formats, naming conventions the Generator must follow]

## Current as of
[Approximate date of the information retrieved]

Be specific. Vague summaries are useless. The Generator needs concrete, accurate details.
"""


class Researcher:

    def __init__(self, router: LiteLLMRouter, emitter: Emitter, google_api_key: Optional[str] = None):
        self.router         = router
        self.emitter        = emitter
        self.google_api_key = google_api_key

    async def run(self, queries: List[str], config: JobConfig) -> str:
        """
        Execute research queries and return a consolidated research brief.

        Args:
            queries: List of search queries from Interpreter.
            config:  Job config for context.

        Returns:
            Research brief string fed to Generator.
        """
        if not queries:
            return ""

        self.emitter.agent_status(AgentID.RESEARCHER, AgentStatus.RUNNING, current_task="Retrieving information")
        self.emitter.log(AgentID.RESEARCHER, f"Executing {len(queries)} research queries via Gemini.")

        results: List[str] = []

        # Run queries concurrently but cap at 3 parallel to avoid rate limits
        semaphore = asyncio.Semaphore(3)

        async def fetch(query: str) -> str:
            async with semaphore:
                return await self._search(query, config.prompt)

        tasks   = [fetch(q) for q in queries]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions
        valid_results = []
        for q, r in zip(queries, results):
            if isinstance(r, Exception):
                self.emitter.log(AgentID.RESEARCHER, f"Query failed: '{q}' — {r}")
                log.error(f"Researcher query failed: {q}: {r}")
            else:
                valid_results.append(str(r))

        if not valid_results:
            self.emitter.warning("Researcher", "All queries failed. Proceeding without live data.")
            self.emitter.agent_status(AgentID.RESEARCHER, AgentStatus.FAILED)
            return ""

        # Consolidate if multiple queries
        if len(valid_results) == 1:
            brief = valid_results[0]
        else:
            brief = await self._consolidate(valid_results, queries, config.prompt)

        self.emitter.log(AgentID.RESEARCHER, f"Research complete. Brief: {len(brief)} chars.")
        self.emitter.agent_status(
            AgentID.RESEARCHER,
            AgentStatus.DONE,
            model_used="gemini/gemini-2.5-pro",
            current_task="Research complete",
        )

        return brief

    async def _search(self, query: str, user_prompt: str) -> str:
        """
        Execute a single search query using Gemini with Google Search grounding.
        """
        self.emitter.log(AgentID.RESEARCHER, f"Searching: '{query}'")

        try:
            # Gemini with Google Search grounding via litellm
            # The tool config enables Gemini's built-in search grounding
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Dataset context: {user_prompt}\n\n"
                        f"Research query: {query}\n\n"
                        f"Search the web and provide a detailed, factual response. "
                        f"Include specific facts, numbers, examples, and current information. "
                        f"Note the approximate date of the information."
                    )
                }
            ]

            # Use Gemini with grounding tools
            response = await litellm.acompletion(
                model="gemini/gemini-2.5-pro",
                messages=messages,
                max_tokens=3000,
                temperature=0.1,
                tools=[{
                    "googleSearch": {}    # Gemini's built-in search grounding
                }],
                api_key=self.google_api_key,
            )

            return response.choices[0].message.content or ""

        except Exception as e:
            # Fallback: try without grounding tool (model may still have recent knowledge)
            log.warning(f"Gemini search grounding failed for '{query}': {e}. Trying without grounding.")
            try:
                response = await litellm.acompletion(
                    model="gemini/gemini-2.5-flash",
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Answer this research query as accurately as possible. "
                            f"Provide specific facts and examples. Note if your knowledge "
                            f"may be outdated.\n\nQuery: {query}\n\nContext: {user_prompt}"
                        )
                    }],
                    max_tokens=2000,
                    temperature=0.1,
                    api_key=self.google_api_key,
                )
                return response.choices[0].message.content or ""
            except Exception as e2:
                raise RuntimeError(f"Both grounded and fallback search failed: {e2}")

    async def _consolidate(self, results: List[str], queries: List[str], user_prompt: str) -> str:
        """Merge multiple query results into a single coherent brief."""
        self.emitter.log(AgentID.RESEARCHER, "Consolidating research findings.")

        combined = "\n\n---\n\n".join(
            f"Query: {q}\n\n{r}" for q, r in zip(queries, results)
        )

        try:
            response = await litellm.acompletion(
                model="gemini/gemini-2.5-flash",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Dataset context: {user_prompt}\n\n"
                            f"Below are research findings for {len(queries)} queries:\n\n"
                            f"{combined}\n\n"
                            f"Consolidate these into a single structured research brief following "
                            f"the required output format. Eliminate redundancy, highlight the most "
                            f"important facts, and organise by relevance to the dataset generation task."
                        )
                    }
                ],
                max_tokens=4000,
                temperature=0.1,
                api_key=self.google_api_key,
            )
            return response.choices[0].message.content or combined
        except Exception as e:
            log.warning(f"Consolidation failed: {e}. Returning raw concatenation.")
            return combined
