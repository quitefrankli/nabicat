#!/usr/bin/env python3
"""
Migration script to populate last_modified field for metrics that don't have it.

This script should be run once to migrate existing metrics data to include
the last_modified field. It sets last_modified to creation_date for any
metric that doesn't have last_modified populated.

Usage:
    python scripts/migrate_metrics_last_modified.py

Or from project root:
    python -m scripts.migrate_metrics_last_modified
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from web_app.data_interface import DataInterface
from web_app.metrics.data_interface import DataInterface as MetricsDataInterface
from web_app.metrics.app_data import Metrics


def migrate_user_metrics(user):
    """Migrate metrics for a single user."""
    di = MetricsDataInterface()
    
    try:
        metrics = di.load_data(user)
    except Exception as e:
        print(f"  Error loading metrics for {user.id}: {e}")
        return False
    
    modified = False
    for metric in metrics.metrics.values():
        if metric.last_modified is None:
            metric.last_modified = metric.creation_date
            modified = True
            print(f"  Updated metric '{metric.name}' (id={metric.id}): "
                  f"last_modified set to {metric.creation_date}")
    
    if modified:
        try:
            di.save_data(metrics, user)
            print(f"  Saved updated metrics for {user.id}")
        except Exception as e:
            print(f"  Error saving metrics for {user.id}: {e}")
            return False
    else:
        print(f"  No migration needed for {user.id}")
    
    return True


def main():
    print("Starting metrics last_modified migration...")
    print("-" * 50)
    
    # Load all users
    base_di = DataInterface()
    users = base_di.load_users()
    
    if not users:
        print("No users found.")
        return
    
    print(f"Found {len(users)} user(s) to process.")
    print()
    
    success_count = 0
    error_count = 0
    
    for username, user in users.items():
        print(f"Processing user: {username}")
        
        if migrate_user_metrics(user):
            success_count += 1
        else:
            error_count += 1
        
        print()
    
    print("-" * 50)
    print(f"Migration complete!")
    print(f"  Successful: {success_count}")
    print(f"  Errors: {error_count}")
    
    if error_count > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
