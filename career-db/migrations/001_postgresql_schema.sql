-- Port of db/init/001_career_schema.sql and db/migrations/002_stage4_customer_pptx_templates.sql
-- PostgreSQL 17; run once only after backup / on new empty bwg_career database.
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE tenant (
 tenant_id CHAR(36) PRIMARY KEY, tenant_code VARCHAR(60) NOT NULL UNIQUE,
 tenant_name VARCHAR(160) NOT NULL, created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE employee (
 employee_id CHAR(36) PRIMARY KEY, tenant_id CHAR(36) NOT NULL REFERENCES tenant(tenant_id),
 employee_key VARCHAR(80) NOT NULL, full_name VARCHAR(100) NOT NULL,
 birth_date DATE, position_title VARCHAR(100), engineer_grade VARCHAR(100),
 profile_version INT NOT NULL DEFAULT 0 CHECK(profile_version >= 0),
 created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(tenant_id,employee_key), UNIQUE(tenant_id,employee_id)
);
CREATE TABLE profile_import (
 import_id CHAR(36) PRIMARY KEY, tenant_id CHAR(36) NOT NULL,
 employee_id CHAR(36) NOT NULL, source_file_name VARCHAR(255) NOT NULL,
 source_sha256 CHAR(64) NOT NULL, source_storage_key VARCHAR(512) NOT NULL,
 template_version VARCHAR(60) NOT NULL,
 status VARCHAR(20) NOT NULL DEFAULT 'UPLOADED' CHECK (status IN ('UPLOADED','PARSED','NEEDS_REVIEW','APPROVED','INDEX_PENDING','INDEXED','REJECTED','FAILED')),
 parsed_json JSONB, issue_count INT NOT NULL DEFAULT 0,
 reviewed_by VARCHAR(120), reviewed_at TIMESTAMP(3),
 created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(tenant_id,employee_id,source_sha256), UNIQUE(tenant_id,import_id),
 FOREIGN KEY(tenant_id,employee_id) REFERENCES employee(tenant_id,employee_id)
);
CREATE TABLE employee_education (
 education_id CHAR(36) PRIMARY KEY, tenant_id CHAR(36) NOT NULL, employee_id CHAR(36) NOT NULL,
 import_id CHAR(36) NOT NULL, level_name VARCHAR(80), school_name VARCHAR(200), major VARCHAR(200),
 admission_date DATE, graduation_date DATE, completion_status VARCHAR(100),
 source_row INT NOT NULL, is_current BOOLEAN NOT NULL DEFAULT TRUE,
 UNIQUE(import_id,source_row),
 FOREIGN KEY(tenant_id,employee_id) REFERENCES employee(tenant_id,employee_id),
 FOREIGN KEY(tenant_id,import_id) REFERENCES profile_import(tenant_id,import_id)
);
CREATE TABLE employee_certification (
 certification_id CHAR(36) PRIMARY KEY, tenant_id CHAR(36) NOT NULL, employee_id CHAR(36) NOT NULL,
 import_id CHAR(36) NOT NULL, certification_name VARCHAR(200) NOT NULL, issuer VARCHAR(200),
 obtained_date DATE, source_row INT NOT NULL, is_current BOOLEAN NOT NULL DEFAULT TRUE,
 UNIQUE(import_id,source_row),
 FOREIGN KEY(tenant_id,employee_id) REFERENCES employee(tenant_id,employee_id),
 FOREIGN KEY(tenant_id,import_id) REFERENCES profile_import(tenant_id,import_id)
);
CREATE TABLE career_project (
 project_id CHAR(36) PRIMARY KEY, tenant_id CHAR(36) NOT NULL, employee_id CHAR(36) NOT NULL,
 import_id CHAR(36) NOT NULL, project_name VARCHAR(500) NOT NULL,
 start_date DATE, end_date DATE, source_duration_text VARCHAR(100),
 role_text VARCHAR(500), technology_text TEXT, client_name VARCHAR(250),
 source_row INT NOT NULL, duplicate_suspected BOOLEAN NOT NULL DEFAULT FALSE,
 approval_status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK(approval_status IN ('PENDING','APPROVED','EXCLUDED')),
 is_current BOOLEAN NOT NULL DEFAULT TRUE,
 project_version INT NOT NULL DEFAULT 1 CHECK(project_version >= 0),
 created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK (start_date IS NULL OR end_date IS NULL OR end_date >= start_date),
 UNIQUE(import_id,source_row), UNIQUE(tenant_id,project_id),
 FOREIGN KEY(tenant_id,employee_id) REFERENCES employee(tenant_id,employee_id),
 FOREIGN KEY(tenant_id,import_id) REFERENCES profile_import(tenant_id,import_id)
);
CREATE INDEX ix_projects_tenant_date ON career_project(tenant_id,is_current,start_date);
CREATE INDEX ix_projects_tenant_client ON career_project(tenant_id,client_name);
CREATE TABLE profile_validation_issue (
 issue_id CHAR(36) PRIMARY KEY, tenant_id CHAR(36) NOT NULL, import_id CHAR(36) NOT NULL,
 issue_code VARCHAR(80) NOT NULL, severity VARCHAR(10) NOT NULL CHECK(severity IN ('INFO','WARNING','ERROR')),
 source_cell VARCHAR(40), source_row INT, message VARCHAR(1000) NOT NULL,
 resolution VARCHAR(12) NOT NULL DEFAULT 'OPEN' CHECK(resolution IN ('OPEN','ACCEPTED','CORRECTED','IGNORED')),
 resolved_by VARCHAR(120), resolved_at TIMESTAMP(3),
 FOREIGN KEY(tenant_id,import_id) REFERENCES profile_import(tenant_id,import_id)
);
CREATE INDEX ix_issue_review ON profile_validation_issue(tenant_id,import_id,severity,resolution);
CREATE TABLE vector_index_outbox (
 event_id CHAR(36) PRIMARY KEY, tenant_id CHAR(36) NOT NULL, project_id CHAR(36) NOT NULL,
 project_version INT NOT NULL CHECK(project_version >= 0),
 operation VARCHAR(10) NOT NULL CHECK(operation IN ('UPSERT','DELETE')),
 status VARCHAR(12) NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','PROCESSING','DONE','FAILED')),
 attempts INT NOT NULL DEFAULT 0, last_error VARCHAR(1000),
 created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP, processed_at TIMESTAMP(3),
 UNIQUE(tenant_id,project_id,project_version,operation),
 FOREIGN KEY(tenant_id,project_id) REFERENCES career_project(tenant_id,project_id)
);
CREATE INDEX ix_outbox_pending ON vector_index_outbox(status,created_at);
CREATE TABLE resume_template (
 template_id CHAR(36) PRIMARY KEY, tenant_id CHAR(36) NOT NULL REFERENCES tenant(tenant_id),
 template_code VARCHAR(80) NOT NULL, template_version VARCHAR(32) NOT NULL,
 template_type VARCHAR(10) NOT NULL CHECK(template_type IN ('MARKDOWN','XLSX','DOCX')),
 template_config JSONB NOT NULL, is_active BOOLEAN NOT NULL DEFAULT TRUE,
 UNIQUE(tenant_id,template_code,template_version)
);
CREATE TABLE career_pptx_template_registry (
 template_id VARCHAR(180) PRIMARY KEY, tenant_id CHAR(36) NOT NULL REFERENCES tenant(tenant_id),
 customer_name VARCHAR(120) NOT NULL, display_name VARCHAR(120) NOT NULL,
 original_file_name VARCHAR(240) NOT NULL, file_sha256 CHAR(64) NOT NULL,
 file_size BIGINT NOT NULL CHECK(file_size >= 0),
 template_version INT NOT NULL DEFAULT 1 CHECK(template_version >= 0),
 analysis_status VARCHAR(20) NOT NULL CHECK(analysis_status IN ('READY','NEEDS_MAPPING','INVALID')),
 layout_mode VARCHAR(30) NOT NULL DEFAULT '', is_active BOOLEAN NOT NULL DEFAULT TRUE,
 created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_customer_templates ON career_pptx_template_registry(tenant_id,is_active,updated_at);
COMMIT;
