import asyncio
import logging

from app.llm import get_chat_model

logging.basicConfig(level=logging.INFO)


async def main():
    try:
        llm = get_chat_model()
        response = await llm.ainvoke("Reply with exactly: OK")
        print("LLM response:", response.content)
    except Exception:
        logging.exception("Azure OpenAI smoke test failed")
        raise


if __name__ == "__main__":
    asyncio.run(main())