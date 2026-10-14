# Migration Examples

Concrete code for the patterns referenced from `SKILL.md`. Adapt to the
project's actual migration tool (`{{MIGRATION_TOOL}}`) per
`.agentry/project/stack.md`.

## Pattern A - Create Table

Pseudocode:

```
create table <resources>:
    primary key id
    foreign key user_id   -> users(id)   ON DELETE CASCADE
    foreign key owner_id  -> owners(id)  ON DELETE SET NULL nullable
    string name (required, <=255)
    text description (nullable)
    string type (required, <=50)
    date starts_at (required)
    boolean is_active (default true)
    timestamp created_at, updated_at
    timestamp deleted_at (nullable, soft delete)

    index (user_id, is_active)
    index (type)

down:
    drop table if exists <resources>
```

`[EXAMPLE - Laravel]`

```php
Schema::create('resources', function (Blueprint $table) {
    $table->id();
    $table->foreignId('user_id')->constrained()->cascadeOnDelete();
    $table->foreignId('owner_id')->nullable()->constrained()->nullOnDelete();
    $table->string('name', 255);
    $table->text('description')->nullable();
    $table->string('type', 50);
    $table->date('starts_at');
    $table->boolean('is_active')->default(true);
    $table->timestamps();
    $table->softDeletes();

    $table->index(['user_id', 'is_active']);
    $table->index('type');
});
```

`[EXAMPLE - TypeORM]`

```ts
await queryRunner.createTable(new Table({
  name: 'resources',
  columns: [
    { name: 'id', type: 'bigint', isPrimary: true, isGenerated: true, generationStrategy: 'increment' },
    { name: 'user_id', type: 'bigint' },
    { name: 'name', type: 'varchar', length: '255' },
    { name: 'type', type: 'varchar', length: '50' },
    { name: 'starts_at', type: 'date' },
    { name: 'is_active', type: 'boolean', default: true },
    { name: 'created_at', type: 'timestamp', default: 'CURRENT_TIMESTAMP' },
    { name: 'updated_at', type: 'timestamp', default: 'CURRENT_TIMESTAMP' },
    { name: 'deleted_at', type: 'timestamp', isNullable: true },
  ],
  indices: [
    { columnNames: ['user_id', 'is_active'] },
    { columnNames: ['type'] },
  ],
  foreignKeys: [
    { columnNames: ['user_id'], referencedTableName: 'users', referencedColumnNames: ['id'], onDelete: 'CASCADE' },
  ],
}));
```

`[EXAMPLE - Alembic]`

```python
op.create_table(
    'resources',
    sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column('user_id', sa.BigInteger, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
    sa.Column('name', sa.String(255), nullable=False),
    sa.Column('type', sa.String(50), nullable=False),
    sa.Column('starts_at', sa.Date, nullable=False),
    sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    sa.Column('updated_at', sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    sa.Column('deleted_at', sa.DateTime, nullable=True),
)
op.create_index('ix_resources_user_is_active', 'resources', ['user_id', 'is_active'])
op.create_index('ix_resources_type', 'resources', ['type'])
```

## Pattern B - Add Column(s)

New columns on existing tables should be `nullable` OR have a `default`, so
the migration runs safely against non-empty tables.

```
alter table <resources>:
    add column priority smallint unsigned default 0
    add column timezone varchar(100) default 'UTC'

down:
    alter table <resources>:
        drop column priority
        drop column timezone
```

## Pattern C - Add Index

```
alter table <entries>:
    add index (resource_id, date) name 'entries_resource_date_idx'

down:
    alter table <entries>:
        drop index 'entries_resource_date_idx'
```

## Pattern D - Add Foreign Key

```
alter table <child>:
    add column parent_id nullable references <parent>(id) ON DELETE SET NULL

down:
    alter table <child>:
        drop foreign key on parent_id
        drop column parent_id
```

## Pattern E - Add Unique Constraint

```
alter table <entries>:
    add unique (item_id, date) name 'entries_item_date_unique'

down:
    alter table <entries>:
        drop unique 'entries_item_date_unique'
```

## Column Type Guidelines

Pick the smallest type that fits. Avoid over-sizing.

| Need | Typical type |
|---|---|
| Auto-increment PK | `bigint unsigned` / `serial` / `identity` |
| FK to auto-increment PK | match the PK type exactly |
| Short text, <=255 | `varchar(255)` |
| Long text | `text` / `longtext` / `clob` |
| Boolean flag | `boolean` / `tinyint(1)` |
| Small counter, <65k | `smallint unsigned` |
| Integer | `int` / `integer` |
| Money / fixed precision | `decimal(p, s)` (never `float`) |
| Date only | `date` |
| Date + time | `timestamp` / `datetimeoffset` |
| Structured data | `json` / `jsonb` |
| UUID | `uuid` (native) or `char(36)` |

## Migration Naming Conventions

- `create_<table>_table` - new table
- `add_<col>_to_<table>_table` - add column(s)
- `add_index_to_<table>_table` - add index
- `add_<ref>_fk_to_<table>_table` - add foreign key
- `modify_<col>_in_<table>_table` - change column type/default
- `rename_<old>_to_<new>_in_<table>_table` - rename
- `remove_<col>_from_<table>_table` - drop column (approval required)

## Change Classification

| Change | Reversible? | Needs approval? |
|---|---|---|
| New table | Yes (drop) | No |
| Add column(s) | Yes (drop columns) | No |
| Add index | Yes (drop index) | No |
| Add foreign key | Yes (drop FK) | No |
| Add unique constraint | Yes (drop unique) | No |
| Modify column | Tricky - preserve full definition | Usually |
| Rename column/table | Yes, but breaks deployed code - coordinate | Yes |
| Drop column/table with data | Destructive | Yes - explicit |

## Deploy Actions Format

```
**Deploy actions:**
- Schema migration - run `<migration tool> migrate` (adds `<N>` new migrations, listed below)
  - YYYY_MM_DD_HHMMSS_add_priority_to_resources_table
  - YYYY_MM_DD_HHMMSS_add_entries_item_date_unique
- No data migration
- No new env vars
- No queue restart needed
```

See `rules/git-workflow.md` for the full Deploy Actions format.
