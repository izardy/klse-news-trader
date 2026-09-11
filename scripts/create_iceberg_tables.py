#!/usr/bin/env python3
"""
Stage 2: Create Iceberg tables with EXACT same schema as SQLite.
Uses S3-backed SqlCatalog (same pattern as DPN foundation komunikasi-scraper).

Each table gets its own catalog.db at s3://helang/{table_name}/catalog.db
Tables are in namespace "klse", catalog name "klse".
"""
import os
import sys
import json
import logging
import tempfile

import boto3
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import (
    NestedField, StringType, LongType, DoubleType, IntegerType,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("iceberg_setup")

# ─── Config (matches DPN foundation pattern) ─────────────────────────────────
BUCKET = "helang"
WAREHOUSE = f"s3://{BUCKET}"
NAMESPACE = "klse"
CATALOG_NAME = "klse"
REGION = "ap-southeast-1"


# ─── Schema Definitions (match SQLite exactly) ───────────────────────────────

def company_metrics_schema():
    """Match SQLite company_metrics table columns."""
    return Schema(
        NestedField(1, "id", IntegerType(), required=False),
        NestedField(2, "symbol", StringType(), required=False),
        NestedField(3, "report_id", IntegerType(), required=False),
        NestedField(4, "metric", StringType(), required=False),
        NestedField(5, "layer", StringType(), required=False),
        NestedField(6, "value", DoubleType(), required=False),
        NestedField(7, "currency", StringType(), required=False),
        NestedField(8, "unit", StringType(), required=False),
        NestedField(9, "fiscal_year", StringType(), required=False),
        NestedField(10, "period", StringType(), required=False),
        NestedField(11, "source_page", IntegerType(), required=False),
        NestedField(12, "source_section", StringType(), required=False),
        NestedField(13, "reported_or_calculated", StringType(), required=False),
        NestedField(14, "definition", StringType(), required=False),
        NestedField(15, "extraction_source", StringType(), required=False),
        NestedField(16, "raw_text", StringType(), required=False),
        NestedField(17, "confidence", DoubleType(), required=False),
        NestedField(18, "extracted_at", StringType(), required=False),
    )


def company_relationships_schema():
    """Match SQLite company_relationships table columns."""
    return Schema(
        NestedField(1, "id", IntegerType(), required=False),
        NestedField(2, "symbol", StringType(), required=False),
        NestedField(3, "related_name", StringType(), required=False),
        NestedField(4, "relationship_type", StringType(), required=False),
        NestedField(5, "fiscal_year", StringType(), required=False),
        NestedField(6, "stake_pct", DoubleType(), required=False),
        NestedField(7, "effective_stake_pct", DoubleType(), required=False),
        NestedField(8, "source_page", IntegerType(), required=False),
        NestedField(9, "source_section", StringType(), required=False),
        NestedField(10, "report_id", IntegerType(), required=False),
        NestedField(11, "extracted_at", StringType(), required=False),
    )


def company_directors_schema():
    """Match SQLite company_directors table columns."""
    return Schema(
        NestedField(1, "id", IntegerType(), required=False),
        NestedField(2, "symbol", StringType(), required=False),
        NestedField(3, "director_name", StringType(), required=False),
        NestedField(4, "fiscal_year", StringType(), required=False),
        NestedField(5, "role", StringType(), required=False),
        NestedField(6, "title", StringType(), required=False),
        NestedField(7, "tenure_years", DoubleType(), required=False),
        NestedField(8, "remuneration_mym_real", DoubleType(), required=False),
        NestedField(9, "independence", StringType(), required=False),
        NestedField(10, "source_page", IntegerType(), required=False),
        NestedField(11, "source_section", StringType(), required=False),
        NestedField(12, "report_id", IntegerType(), required=False),
        NestedField(13, "extracted_at", StringType(), required=False),
    )


def company_shareholders_schema():
    """Match SQLite company_shareholders table columns."""
    return Schema(
        NestedField(1, "id", IntegerType(), required=False),
        NestedField(2, "symbol", StringType(), required=False),
        NestedField(3, "shareholder_name", StringType(), required=False),
        NestedField(4, "fiscal_year", StringType(), required=False),
        NestedField(5, "shareholder_type", StringType(), required=False),
        NestedField(6, "stake_percent", DoubleType(), required=False),
        NestedField(7, "shares_held", DoubleType(), required=False),
        NestedField(8, "is_substantial", IntegerType(), required=False),
        NestedField(9, "is_controlling", IntegerType(), required=False),
        NestedField(10, "source_page", IntegerType(), required=False),
        NestedField(11, "source_section", StringType(), required=False),
        NestedField(12, "report_id", IntegerType(), required=False),
        NestedField(13, "extracted_at", StringType(), required=False),
    )


def annual_reports_schema():
    """Match SQLite annual_reports table columns + s3_key for pipeline tracking."""
    return Schema(
        NestedField(1, "id", IntegerType(), required=False),
        NestedField(2, "symbol", StringType(), required=False),
        NestedField(3, "title", StringType(), required=False),
        NestedField(4, "announcement_date", StringType(), required=False),
        NestedField(5, "pdf_url", StringType(), required=False),
        NestedField(6, "local_path", StringType(), required=False),
        NestedField(7, "file_size", IntegerType(), required=False),
        NestedField(8, "processed", IntegerType(), required=False),
        NestedField(9, "downloaded_at", StringType(), required=False),
        NestedField(10, "created_at", StringType(), required=False),
        NestedField(11, "s3_key", StringType(), required=False),
        NestedField(12, "fiscal_year", StringType(), required=False),
    )


# ─── Catalog Management (exact DPN foundation pattern) ───────────────────────

TABLES = {
    "company_metrics": (company_metrics_schema(), "fiscal_year"),
    "company_relationships": (company_relationships_schema(), "fiscal_year"),
    "company_directors": (company_directors_schema(), "fiscal_year"),
    "company_shareholders": (company_shareholders_schema(), "fiscal_year"),
    "annual_reports": (annual_reports_schema(), "fiscal_year"),
}


def open_catalog(s3, table_name, tmpdir):
    """Open catalog for a specific table (each table has its own catalog.db)."""
    local_db = os.path.join(tmpdir, "catalog.db")
    catalog_key = f"{table_name}/catalog.db"
    try:
        s3.download_file(BUCKET, catalog_key, local_db)
        log.info(f"  Downloaded existing catalog: s3://{BUCKET}/{catalog_key}")
    except Exception:
        log.info(f"  No existing catalog for {table_name} — will create new")

    catalog = SqlCatalog(
        CATALOG_NAME,
        uri=f"sqlite:///{local_db}",
        warehouse=WAREHOUSE,
    )
    try:
        catalog.create_namespace(NAMESPACE)
    except Exception:
        pass
    return catalog, catalog_key, local_db


def get_or_create_table(catalog, table_name, schema, partition_col="fiscal_year"):
    """Get existing table or create new one with partition."""
    full_name = f"{NAMESPACE}.{table_name}"
    location = f"s3://{BUCKET}/{table_name}"

    try:
        tbl = catalog.load_table(full_name)
        log.info(f"  Table {full_name} already exists")
        return tbl
    except Exception:
        pass

    # Find the partition field
    part_field = None
    for f in schema.fields:
        if f.name == partition_col:
            part_field = f
            break

    if part_field is None:
        tbl = catalog.create_table(full_name, schema=schema, location=location)
        log.info(f"  Created table {full_name} (no partition)")
        return tbl

    spec = PartitionSpec(
        PartitionField(
            source_id=part_field.field_id,
            field_id=1000,
            transform=IdentityTransform(),
            name=partition_col,
        )
    )
    tbl = catalog.create_table(full_name, schema=schema, location=location, partition_spec=spec)
    log.info(f"  Created table {full_name} (partitioned by {partition_col})")
    return tbl


def create_all_tables():
    """Create all 5 Iceberg tables."""
    s3 = boto3.client("s3", region_name=REGION)

    for table_name, (schema, part_col) in TABLES.items():
        log.info(f"Creating table: {table_name}")
        with tempfile.TemporaryDirectory(prefix=f"klse-{table_name}-") as tmpdir:
            catalog, catalog_key, local_db = open_catalog(s3, table_name, tmpdir)
            try:
                get_or_create_table(catalog, table_name, schema, part_col)
                # Upload catalog
                s3.upload_file(local_db, BUCKET, catalog_key)
                log.info(f"  Catalog saved -> s3://{BUCKET}/{catalog_key}")
            finally:
                try:
                    catalog.close()
                except Exception:
                    pass

    log.info("All Iceberg tables created successfully")
    return list(TABLES.keys())


if __name__ == "__main__":
    created = create_all_tables()
    print(f"Created tables: {created}")
