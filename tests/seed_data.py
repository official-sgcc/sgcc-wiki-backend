def seed_tag(name):
    from sqlmodel import Session
    from sgcc_wiki_backend.core.database import engine
    from sgcc_wiki_backend.schemas.tags import WikiTag
    with Session(engine) as session:
        session.add(WikiTag(name=name))
        session.commit()
