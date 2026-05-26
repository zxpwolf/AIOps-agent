#!/usr/bin/env python3
import asyncio
import sys
sys.path.insert(0, '/Users/admin/Documents/projects/AIOps/AIOps-agent/src')

from aiops_agent.llm.qwen import QwenProvider
from aiops_agent.llm.provider import LLMProviderFactory, ChatResponse
from aiops_agent.core.task_planner import TaskPlanner
from aiops_agent.models.schemas import Message
from aiops_agent.skills.registry import SkillRegistry
from aiops_agent.models.schemas import SkillDefinition
from aiops_agent.skills.base import SkillInstance
from aiops_agent.models.schemas import ValidationResult

class StubSkill(SkillInstance):
    async def execute(self, input_data: dict) -> dict:
        return {"ok": True}

    async def validate(self, input_data: dict):
        return ValidationResult(valid=True)

async def main():
    # Load API key from settings.yaml
    import yaml
    with open('config/settings.yaml') as f:
        config = yaml.safe_load(f)
    
    qwen_config = config.get('llm', {}).get('providers', {}).get('qwen', {})
    api_key = qwen_config.get('api_key', '')
    
    print(f"API Key in config: {api_key[:20] if api_key else 'NOT SET'}")
    
    if not api_key:
        print("No API key found in settings.yaml")
        return
    
    # Create Qwen provider
    qwen = QwenProvider(api_key=api_key)
    
    # Create factory
    factory = LLMProviderFactory()
    factory.register("qwen", qwen)
    factory.set_primary("qwen")
    
    # Create registry
    registry = SkillRegistry()
    await registry.register(
        SkillDefinition(
            skill_name="monitoring",
            description="Monitoring skill",
            version="1.0.0",
            capabilities=["query_metrics"],
            required_permissions=[]
        ),
        StubSkill()
    )
    
    # Test decomposition
    planner = TaskPlanner(factory, registry)
    
    print("\n--- Testing Qwen decomposition ---")
    try:
        plan = await planner.decompose("查看 ECS 实例的 CPU 使用率")
        print(f"Decomposition successful!")
        print(f"Number of subtasks: {len(plan.sub_tasks)}")
        for task in plan.sub_tasks:
            print(f"  - {task.task_id}: {task.skill_name} - {task.action}")
    except Exception as e:
        print(f"Decomposition failed: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
