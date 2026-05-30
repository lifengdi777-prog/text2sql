from sqlalchemy.orm import DeclarativeBase

#它是在定义一个 SQLAlchemy ORM 模型的基类。
#之后所有数据库表对应的 Python 类都继承它。
class Base(DeclarativeBase):
    pass