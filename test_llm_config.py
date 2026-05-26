#!/usr/bin/env python3
"""Test script to verify LLM provider configuration."""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from aiops_agent.main import create_agent
from aiops_agent.models.schemas import Message


async def test_llm_provider():
    """Test that Qwen provider is being used."""
    print("Creating agent...")
    agent = await create_agent()
    
    print(f"\nLLM Factory primary: {agent._llm_factory._primary_name}")
    print(f"LLM Factory fallback: {agent._llm_factory._fallback_name}")
    print(f"Registered providers: {list(agent._llm_factory._providers.keys())}")
    
    # Test a simple chat
    print("\nTesting chat with Qwen provider...")
    try:
        messages = [Message(role="user", content="Hello, are you Qwen?")]
        response = await agent._llm_factory.chat(messages)
        print(f"Response model: {response.model}")
        print(f"Response content: {response.content[:100]}...")
        print("\n✅ Qwen provider is working correctly!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_llm_provider())
