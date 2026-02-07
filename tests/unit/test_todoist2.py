"""Unit tests for todoist2 module"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta, date

from web_app.app import app
from web_app.users import User
from web_app.todoist2 import (
    todoist2_api,
    _get_filtered_summary_goals,
    _goals_to_blocks,
    _get_completed_goals,
    _completed_goals_to_blocks,
    PAGE_SIZE
)
from web_app.todoist2.app_data import Goal, GoalState, Recurrence, Goals


@pytest.fixture
def client():
    """Create a test client for the Flask app"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_user():
    """Create a test user"""
    return User(username='testuser', password='testpass', folder='test_folder', is_admin=False)


@pytest.fixture
def test_goal():
    """Create a test goal"""
    return Goal(
        id=1,
        name='Complete project',
        state=GoalState.ACTIVE,
        description='Finish the project',
        creation_date=datetime(2026, 1, 1),
        last_modified=datetime(2026, 2, 1)
    )


@pytest.fixture
def test_goals(test_goal):
    """Create multiple test goals"""
    goals = [
        test_goal,
        Goal(
            id=2,
            name='Exercise',
            state=GoalState.COMPLETED,
            description='Daily exercise',
            creation_date=datetime(2026, 1, 15),
            last_modified=datetime(2026, 2, 2),
            completion_date=datetime(2026, 2, 2)
        ),
        Goal(
            id=3,
            name='Read book',
            state=GoalState.BACKLOGGED,
            description='Read the book',
            creation_date=datetime(2026, 1, 20),
            last_modified=datetime(2026, 2, 3)
        ),
    ]
    return Goals(goals={goal.id: goal for goal in goals})


class TestTodoist2HelperFunctions:
    """Tests for todoist2 helper functions"""

    @patch('web_app.todoist2.DataInterface')
    def test_get_filtered_summary_goals(self, mock_di, test_user, test_goals):
        """Test filtering summary goals"""
        mock_instance = Mock()
        mock_instance.load_data.return_value = test_goals
        mock_di.return_value = mock_instance

        goals = _get_filtered_summary_goals(test_user)
        
        # Should filter and sort goals
        assert isinstance(goals, list)

    def test_goals_to_blocks_empty(self):
        """Test goals_to_blocks with empty list"""
        blocks = _goals_to_blocks([])
        assert blocks == []

    def test_goals_to_blocks_single_goal(self, test_goal):
        """Test goals_to_blocks with single goal"""
        blocks = _goals_to_blocks([test_goal])
        assert len(blocks) == 1
        assert len(blocks[0][1]) == 1

    def test_goals_to_blocks_multiple_same_date(self, test_goal):
        """Test goals_to_blocks with multiple goals on same date"""
        goal1 = test_goal
        goal2 = Goal(
            id=2,
            name='Another goal',
            state=GoalState.ACTIVE,
            last_modified=goal1.last_modified
        )
        
        blocks = _goals_to_blocks([goal1, goal2])
        assert len(blocks) == 1
        assert len(blocks[0][1]) == 2

    def test_goals_to_blocks_multiple_dates(self, test_goal):
        """Test goals_to_blocks with goals on different dates"""
        goal1 = test_goal
        goal2 = Goal(
            id=2,
            name='Another goal',
            state=GoalState.ACTIVE,
            last_modified=goal1.last_modified + timedelta(days=1)
        )
        
        blocks = _goals_to_blocks([goal1, goal2])
        assert len(blocks) == 2

    @patch('web_app.todoist2.DataInterface')
    def test_get_completed_goals(self, mock_di, test_user, test_goals):
        """Test getting completed goals"""
        mock_instance = Mock()
        mock_instance.load_data.return_value = test_goals
        mock_di.return_value = mock_instance

        goals = _get_completed_goals(test_user)
        
        # Should only return completed goals
        assert all(goal.state == GoalState.COMPLETED for goal in goals)

    def test_completed_goals_to_blocks_empty(self):
        """Test completed_goals_to_blocks with empty list"""
        blocks = _completed_goals_to_blocks([])
        assert blocks == []

    def test_completed_goals_to_blocks_single_goal(self):
        """Test completed_goals_to_blocks with single goal"""
        goal = Goal(
            id=1,
            name='Completed',
            state=GoalState.COMPLETED,
            completion_date=datetime(2026, 2, 1),
            last_modified=datetime.now()
        )
        blocks = _completed_goals_to_blocks([goal])
        assert len(blocks) == 1
        assert len(blocks[0][1]) == 1

    def test_completed_goals_to_blocks_multiple_dates(self):
        """Test completed_goals_to_blocks with multiple dates"""
        goal1 = Goal(
            id=1,
            name='Goal 1',
            state=GoalState.COMPLETED,
            completion_date=datetime(2026, 2, 1),
            last_modified=datetime.now()
        )
        goal2 = Goal(
            id=2,
            name='Goal 2',
            state=GoalState.COMPLETED,
            completion_date=datetime(2026, 2, 2),
            last_modified=datetime.now()
        )
        
        blocks = _completed_goals_to_blocks([goal1, goal2])
        assert len(blocks) == 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
