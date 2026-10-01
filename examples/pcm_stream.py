"""生成一秒静音 PCM，演示分块、显式 commit 和有期限的事件观察。"""

import asyncio
import os

from aha import AsyncAudioHarness, AudioFormat


async def main() -> None:
    async with AsyncAudioHarness(
        base_url=os.environ["AHA_BASE_URL"], api_key=os.getenv("AHA_API_KEY")
    ) as client:
        async with client.transcriptions.connect(
            model=os.environ["AHA_MODEL"],
            audio=AudioFormat.pcm16(16000),
            session={"audio": {"input": {"turn_detection": None}}},
        ) as session:

            async def consume() -> None:
                async for event in session.events():
                    print(event.model_dump())

            reader = asyncio.create_task(consume())
            try:
                await session.send_audio(b"\0\0" * 16000)
                await session.input_audio_buffer.commit()
                try:
                    await asyncio.wait_for(reader, timeout=10)
                except asyncio.TimeoutError:
                    print("观察时间结束；不代表所有结果已排空。")
            finally:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
