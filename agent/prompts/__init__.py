from pathlib import Path
import aiofiles

async def load_prompt(name: str) -> str:
    #获取到当前文件所在的父目录路径，然后拼接上{name}.md，得到完整的文件路径
    file_path = Path(__file__).parent / f"{name}.md"
    async with aiofiles.open(file_path, "r", encoding="utf-8") as fp:
        return await fp.read()
    
