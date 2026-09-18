def seed_tag(name):
    from sqlmodel import Session
    from core.database import engine
    from schemas.tags import WikiTag
    with Session(engine) as session:
        session.add(WikiTag(name=name))
        session.commit()
