from langchain_openai import ChatOpenAI
from conf.app_config import app_config
from core.log import logger

def main():
    print("Hello from wenshu!")
    logger.info("这是一条logger消息.")
    # llm = ChatOpenAI(
    #     base_url=app_config.llm.base_url,
    #     api_key=app_config.llm.api_key,
    #     model=app_config.llm.model_name,
    #     temperature=0.1
    #     )
    
    # result = llm.invoke("Hello, 你是谁?")
    # print(result)


if __name__ == "__main__":
    main()
