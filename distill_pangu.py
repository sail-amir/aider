#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "aiohttp",
#     "tqdm",
# ]
# ///
"""
Custom JSONL inference script for code-edit style tasks with custom endpoint support.

Input:
- JSONL file
- One example per line
- Each line must include a "question" field

Output:
- JSONL file
- One result per input line
- Copies the full input row and appends:
  idx, content, reasoning_content, elapsed_seconds, attempts, max_tokens,
  and optionally usage/provider if returned by the endpoint
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import List, Literal, Optional, TypedDict

import aiohttp
from tqdm import tqdm


Role = Literal["system", "user", "assistant"]


class Message(TypedDict):
    role: Role
    content: str


class CustomEndpointChatModel:
    """
    Custom model class for endpoints with custom headers and optional streaming support.

    Designed for endpoints like:
    http://host:port/.../v1/chat/completions
    """

    def __init__(
        self,
        api_url: str,
        model_name: str,
        csb_token: str,
        auth_token: str = "nokey",
        use_streaming: bool = False,
    ):
        self.api_url = api_url
        self.model_name = model_name
        self.csb_token = csb_token
        self.auth_token = auth_token
        self.use_streaming = use_streaming

    def _format_messages(self, item: dict) -> List[Message]:
        question = item.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError('Each input row must contain a non-empty "question" field.')

        messages: List[Message] = []

        # Add system prompt if provided (optional "system" field on the input row)
        system_prompt = item.get("system")
        if system_prompt and isinstance(system_prompt, str) and system_prompt.strip():
            messages.append({
                "role": "system",
                "content": system_prompt,
            })

        # Add user message
        messages.append({
            "role": "user",
            "content": question,
        })

        return messages

    async def generate_non_streaming(
        self,
        item: dict,
        session: aiohttp.ClientSession,
        **kwargs,
    ) -> tuple[str, str, Optional[dict], Optional[str]]:
        messages = self._format_messages(item)

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 0.8),
            "top_k": kwargs.get("top_k", 20),
            "max_tokens": kwargs.get("max_tokens", 3072),
            "stream": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.auth_token}",
            "csb-token": self.csb_token,
        }

        async with session.post(self.api_url, json=payload, headers=headers) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"API Error {response.status}: {error_text}")

            result = await response.json()

        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Response missing non-empty 'choices'.")

        message = choices[0].get("message", {})
        if not isinstance(message, dict):
            raise ValueError("Response choice missing 'message' object.")

        content = message.get("content", "")
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = str(content)

        reasoning = (
            message.get("reasoning_content")
            or message.get("reasoning")
            or ""
        )
        if reasoning is None:
            reasoning = ""
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)

        usage = result.get("usage")
        provider = result.get("provider")

        return content, reasoning, usage, provider

    async def generate_streaming(
        self,
        item: dict,
        session: aiohttp.ClientSession,
        **kwargs,
    ) -> tuple[str, str, Optional[dict], Optional[str]]:
        messages = self._format_messages(item)

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 0.8),
            "top_k": kwargs.get("top_k", 20),
            "max_tokens": kwargs.get("max_tokens", 3072),
            "stream": True,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.auth_token}",
            "csb-token": self.csb_token,
        }

        full_content = ""
        full_reasoning = ""
        provider: Optional[str] = None
        usage: Optional[dict] = None

        async with session.post(self.api_url, json=payload, headers=headers) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"API Error {response.status}: {error_text}")

            async for raw_line in response.content:
                line = raw_line.decode("utf-8").strip()

                if not line:
                    continue
                if line == "data: [DONE]":
                    continue
                if not line.startswith("data: "):
                    continue

                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                if provider is None and "provider" in data:
                    provider = data.get("provider")

                if "usage" in data and isinstance(data["usage"], dict):
                    usage = data["usage"]

                choices = data.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue

                delta = choices[0].get("delta", {})
                if not isinstance(delta, dict):
                    continue

                content_chunk = delta.get("content", "")
                if content_chunk:
                    full_content += str(content_chunk)

                reasoning_chunk = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if reasoning_chunk:
                    full_reasoning += str(reasoning_chunk)

        return full_content, full_reasoning, usage, provider

    async def generate(
        self,
        item: dict,
        **kwargs,
    ) -> tuple[str, str, Optional[dict], Optional[str]]:
        session = kwargs.pop("_session")

        if self.use_streaming:
            return await self.generate_streaming(item, session, **kwargs)
        return await self.generate_non_streaming(item, session, **kwargs)


async def process_item(
    item: dict,
    idx: int,
    item_hash: str,
    model: CustomEndpointChatModel,
    model_kwargs: dict,
    args: argparse.Namespace,
    batch_sema: asyncio.Semaphore,
    session: aiohttp.ClientSession,
) -> dict:
    async with batch_sema:
        start_time = time.perf_counter()
        max_retries = getattr(args, 'max_retries', 3)
        attempts = 0
        content = ""
        reasoning = ""
        usage = None
        provider = None

        # Retry loop for empty content
        for attempt in range(max_retries):
            attempts = attempt + 1

            try:
                async with asyncio.timeout(args.timeout):
                    content, reasoning, usage, provider = await model.generate(
                        item,
                        _session=session,
                        **model_kwargs,
                    )

                # Check if content and reasoning are valid (non-empty and not an error)
                if (content and content.strip() and
                    reasoning and reasoning.strip() and
                    not content.startswith("<timeout:") and
                    not content.startswith("<http_error:") and
                    not content.startswith("<client_error:") and
                    not content.startswith("<error:") and
                    not content.startswith("<empty response")):
                    break

                # Empty content - retry if not last attempt
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))  # Exponential backoff
                    continue
                else:
                    # Last attempt - mark as empty
                    content = "<empty response after retries>"

            except asyncio.TimeoutError:
                content = f"<timeout: exceeded {args.timeout}s>"
                reasoning = ""
                usage = None
                provider = None
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                break
            except aiohttp.ClientResponseError as e:
                # HTTP error with status code
                content = f"<http_error: {e.status} {e.message}>"
                reasoning = ""
                usage = None
                provider = None
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                break
            except aiohttp.ClientError as e:
                # Connection errors, etc.
                content = f"<client_error: {type(e).__name__}: {str(e)}>"
                reasoning = ""
                usage = None
                provider = None
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                break
            except Exception as e:
                # Catch-all with exception type
                content = f"<error: {type(e).__name__}: {str(e)}>"
                reasoning = ""
                usage = None
                provider = None
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                break

        elapsed_seconds = round(time.perf_counter() - start_time, 2)

        result = {
            **item,
            "idx": idx,
            "item_hash": item_hash,
            "content": content,
            "reasoning_content": reasoning,
            "elapsed_seconds": elapsed_seconds,
            "attempts": attempts,
            "max_tokens": args.max_tokens,
        }

        if usage is not None:
            result["usage"] = usage
        if provider is not None:
            result["provider"] = provider

        return result


def load_jsonl(path: Path) -> list[dict]:
    items: list[dict] = []

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            try:
                item = json.loads(stripped)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on line {line_no} of {path}: {e}") from e

            if not isinstance(item, dict):
                raise ValueError(f"Line {line_no} of {path} is not a JSON object.")

            question = item.get("question")
            if not isinstance(question, str) or not question.strip():
                raise ValueError(
                    f'Line {line_no} of {path} must contain a non-empty "question" field.'
                )

            items.append(item)

    return items


def compute_item_hash(item: dict) -> str:
    """
    Compute a unique hash for an item based on the question field.

    The question field contains the full prompt which uniquely identifies the item.
    """
    question = item.get("question", "")
    if not question:
        # Fallback: hash the entire item if no question field
        question = json.dumps(item, sort_keys=True)

    return hashlib.sha256(question.encode()).hexdigest()[:16]  # First 16 chars


async def main(args: argparse.Namespace) -> None:
    input_path = Path(args.input_file)
    output_path = Path(args.output_file)

    items = load_jsonl(input_path)

    # Resume logic: filter out failed results and keep only valid ones
    existing_hashes = set()
    if output_path.exists():
        print(f"Found existing output file: {output_path}")

        # Read all existing results
        all_results = []
        with output_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    result = json.loads(line.strip())
                    all_results.append(result)
                except json.JSONDecodeError:
                    continue

        print(f"Total existing results: {len(all_results)}")

        # Filter out error results (timeout, http_error, client_error, error, empty response/reasoning)
        valid_results = []
        excluded_count = 0
        for result in all_results:
            content = result.get("content", "")
            reasoning_content = result.get("reasoning_content", "")
            # Check if content or reasoning indicates an error/failure or is empty
            if (not content or not content.strip() or
                not reasoning_content or not reasoning_content.strip() or
                content.startswith("<timeout:") or
                content.startswith("<http_error:") or
                content.startswith("<client_error:") or
                content.startswith("<error:") or
                content.startswith("<empty response")):
                excluded_count += 1
                continue
            valid_results.append(result)

        if excluded_count > 0:
            print(f"Excluded {excluded_count} failed results (will retry)")
            print(f"Valid results retained: {len(valid_results)}")

            # Rewrite output file with only valid results
            print(f"Rewriting output file with valid results only...")
            with output_path.open("w", encoding="utf-8") as f:
                for result in valid_results:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")

        # Build hash set from valid results
        for result in valid_results:
            if "item_hash" in result:
                existing_hashes.add(result["item_hash"])

        print(f"Already processed (valid): {len(existing_hashes)} items")

    # Compute hash for each item and filter to only process new ones
    items_with_data = []
    for idx, item in enumerate(items):
        item_hash = compute_item_hash(item)
        if item_hash not in existing_hashes:
            items_with_data.append((idx, item, item_hash))

    print(f"Total items: {len(items)}")
    print(f"Remaining to process: {len(items_with_data)}")

    if not items_with_data:
        print("All items already processed!")
        return

    models = []
    for api_url in args.api_urls:
        models.append(
            CustomEndpointChatModel(
                api_url=api_url,
                model_name=args.model,
                csb_token=args.csb_token,
                auth_token=args.auth_token,
                use_streaming=args.stream,
            )
        )

    model_kwargs = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "max_tokens": args.max_tokens,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)

    batch_sema = asyncio.Semaphore(args.batch_size)

    # Configure connector with high limits to avoid bottlenecking on network I/O
    # Default aiohttp limits (100 total, 30 per host) are too low for high-throughput scenarios
    connector = aiohttp.TCPConnector(
        limit=args.batch_size + 100,  # Total connections across all hosts
        limit_per_host=max(500, args.batch_size // len(args.api_urls) + 50),  # Per endpoint
        ttl_dns_cache=300,  # Cache DNS for 5 minutes
    )
    timeout = aiohttp.ClientTimeout(total=args.timeout + 10)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks = []
        for idx, item, item_hash in items_with_data:
            model = models[idx % len(models)]
            tasks.append(
                asyncio.create_task(
                    process_item(
                        item=item,
                        idx=idx,
                        item_hash=item_hash,
                        model=model,
                        model_kwargs=model_kwargs,
                        args=args,
                        batch_sema=batch_sema,
                        session=session,
                    )
                )
            )

        # Open in append mode to preserve existing results
        mode = "a" if output_path.exists() else "w"
        with output_path.open(mode, encoding="utf-8") as out_f:
            pbar = tqdm(total=len(tasks), desc="Processing")
            for coro in asyncio.as_completed(tasks):
                result = await coro
                out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
                out_f.flush()
                pbar.update(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Custom JSONL inference script with custom endpoint support"
    )
    parser.add_argument(
        "--input-file",
        type=str,
        required=True,
        help="Input JSONL file",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        required=True,
        help="Output JSONL file",
    )
    parser.add_argument(
        "--api-urls",
        type=str,
        nargs="+",
        required=True,
        help='One or more API URLs for load balancing (e.g. "http://host1/v1/chat/completions" "http://host2/v1/chat/completions")',
    )
    parser.add_argument(
        "--model",
        type=str,
        default="pangu_auto",
        help="Model name to use in requests",
    )
    parser.add_argument(
        "--csb-token",
        type=str,
        required=True,
        help="CSB token for authentication",
    )
    parser.add_argument(
        "--auth-token",
        type=str,
        default="nokey",
        help='Bearer token for Authorization header (default: "nokey")',
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Use streaming mode",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Concurrent requests limit",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="Sampling temperature",
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=0.8,
        help="Top-p sampling",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=20,
        help="Top-k sampling",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=3072,
        help="Max new tokens to generate",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout in seconds for each request",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum retry attempts for empty/failed responses (default: 3)",
    )

    parsed_args = parser.parse_args()
    asyncio.run(main(parsed_args))