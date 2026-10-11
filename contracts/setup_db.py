"""Create collections, validators and indexes, seed model_policy and price_book, then prove the contract.
Idempotent: validators are re-applied with collMod, seeds use $setOnInsert so admin edits survive.

    uv run python -m contracts.setup_db                      # lane1 lane2 lane3 h1 h2 demo
    uv run python -m contracts.setup_db lane1

The self-check inserts every sample doc (and removes it again) and confirms a bad condition is rejected.
"""
import sys
import time
from copy import deepcopy

from bson import ObjectId
from pymongo import UpdateOne
from pymongo.database import Database
from pymongo.errors import WriteError

from app.db import get_db, is_mock
from contracts.samples import BAD_CONDITION, SAMPLES
from contracts.schemas import INDEXES, VALIDATORS
from contracts.seed import MODEL_POLICY, price_book
from contracts.validate import validate

ALL_DBS = ["lane1", "lane2", "lane3", "h1", "h2", "demo"]
UNIQUE_KEYS = {"model_policy": "task", "product_data": "query_hash", "price_book": "item"}


def ensure_schema(db: Database) -> None:
    existing = set(db.list_collection_names())
    for name, schema in VALIDATORS.items():
        if is_mock():  # mongomock has no document validation
            if name not in existing:
                db.create_collection(name)
            continue
        opts = {"validator": {"$jsonSchema": schema}, "validationAction": "error", "validationLevel": "strict"}
        if name in existing:
            db.command("collMod", name, **opts)
        else:
            db.create_collection(name, **opts)
    for name, specs in INDEXES.items():
        for keys, options in specs:
            db[name].create_index(keys, **options)


def seed(db: Database) -> None:
    for coll, rows, keys in (
        (db.model_policy, MODEL_POLICY, ("task",)),
        (db.price_book, price_book(), ("item", "supplier")),
    ):
        ops = [({k: r[k] for k in keys}, {"$setOnInsert": r}) for r in rows]
        if is_mock():  # mongomock's bulk_write lags pymongo's UpdateOne
            for flt, upd in ops:
                coll.update_one(flt, upd, upsert=True)
        else:
            coll.bulk_write([UpdateOne(flt, upd, upsert=True) for flt, upd in ops])
    # Rows seeded before `params` existed get it once; a params value someone set in Compass is left alone.
    for p in MODEL_POLICY:
        if "params" in p:
            db.model_policy.update_one({"task": p["task"], "params": {"$exists": False}}, {"$set": {"params": p["params"]}})


def self_check(db: Database) -> None:
    for name, sample in SAMPLES.items():
        errs = validate(name, sample)
        if errs:
            raise AssertionError(f"sample {name} fails contracts.validate: {errs}")
        doc = deepcopy(sample)
        doc["_id"] = ObjectId()
        if name in UNIQUE_KEYS:
            doc[UNIQUE_KEYS[name]] += f"__check_{doc['_id']}"
        if name == "sheets":
            doc["page"] = 9999
        db[name].insert_one(doc)
        db[name].delete_one({"_id": doc["_id"]})
    if not validate("conditions", BAD_CONDITION):
        raise AssertionError("contracts.validate accepted BAD_CONDITION")
    if is_mock():
        return
    bad = {**BAD_CONDITION, "_id": ObjectId()}
    try:
        db.conditions.insert_one(bad)
    except WriteError as e:
        if e.code != 121:
            raise
    else:
        db.conditions.delete_one({"_id": bad["_id"]})
        raise AssertionError("MongoDB accepted BAD_CONDITION: validator not enforcing")


def setup(db: Database) -> str:
    t0 = time.perf_counter()
    ensure_schema(db)
    seed(db)
    self_check(db)
    return (
        f"{db.name}: {len(VALIDATORS)} validated collections, indexes ok, "
        f"{db.model_policy.count_documents({})} policies, {db.price_book.count_documents({})} prices, "
        f"samples accepted, bad condition rejected ({time.perf_counter() - t0:.1f}s)"
    )


if __name__ == "__main__":
    for db_name in sys.argv[1:] or ALL_DBS:
        print(setup(get_db(db_name)))
