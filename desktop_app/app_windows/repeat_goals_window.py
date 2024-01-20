import tkinter

from datetime import datetime, timedelta
from tkinter import ttk, Frame, Label, Button, Entry
from typing import *
from playsound import playsound

from desktop_app.goal import Goal
from desktop_app.client import ClientV2
from .goal_widget import GoalWidget, GoalDetailsWindow
from .goals_window import GoalsWindow


class RepeatGoalsWindow(GoalsWindow):
    def __init__(self, master: Any, client: ClientV2, refresh_all: Callable):
        super().__init__(master, 'Repeat Goals', client, refresh_all)
        create_new_repeat_goal_button = Button(self, text="New Repeat Goal", command=self.spawn_new_repeat_goal_creation_window)
        create_new_repeat_goal_button.pack(side=tkinter.TOP)

        # some time may have passed since the last time the repeat goals window was opened
        # retroactively create any repeat child goals that should have been created
        for goal in self.get_all_parent_repeat_goals():
            self.spawn_repeat_child_goals(goal)

    def get_all_parent_repeat_goals(self) -> List[Goal]:
        goals = self.client.fetch_goals()
        goals = [goal for goal in goals if goal.repeat > 0 and goal.parent == -1]
        goals.sort(key=lambda goal: goal.metadata.creation_date.timestamp())
        return goals

    def delete_goal(self, goal_id: int) -> None:
        goal = self.client.fetch_goal(goal_id)
        assert(goal.parent == -1)
        # delete all child goals
        for child_id in goal.children:
            self.client.delete_goal(child_id)
        super().delete_goal(goal_id)

    def spawn_repeat_child_goals(self, goal: Goal) -> None:
        existing_child_goals = [self.client.fetch_goal(child) for child in goal.children]
        limit_date = datetime.now() + timedelta(weeks=2)
        if existing_child_goals:
            child_goal_date = existing_child_goals[-1].daily + timedelta(days=goal.repeat)
            occurrence = int(existing_child_goals[-1].name.split(' ')[-1]) + 1
        else:
            child_goal_date = goal.metadata.creation_date
            occurrence = 1
        while child_goal_date < limit_date:
            child_goal = Goal(id=-1, 
                              name=f"{goal.name} {occurrence}", 
                              state=False, 
                              daily=child_goal_date,
                              description=goal.description)
            child_goal.repeat = 1000 # non-zero value to indicate repeat goal
            child_goal_id = self.client.add_goal(child_goal)
            self.client.setup_parent(child_goal_id, goal.id)

            child_goal_date += timedelta(days=goal.repeat)
            occurrence += 1
    
    def spawn_new_repeat_goal_creation_window(self) -> None:
        top = tkinter.Toplevel(self)
        top.geometry(f"200x200+{self.winfo_rootx()}+{self.winfo_rooty()}")
        top.title("New Repeat Goal")
        goal_name = tkinter.StringVar()
        Label(top, text='Goal Name').pack()
        new_goal_entry = Entry(top, textvariable=goal_name)
        new_goal_entry.focus()
        new_goal_entry.pack()

        Label(top, text='Description').pack()
        description = tkinter.StringVar()
        description_entry = Entry(top, textvariable=description)
        description_entry.pack()

        period_frame = Frame(top)
        period_frame.pack()

        period_label = Label(period_frame, text='Repeat Every (Days):')
        period_label.pack(side=tkinter.LEFT)

        period = tkinter.IntVar()
        period.set(1)
        period_entry = Entry(period_frame, textvariable=period)
        period_entry.pack(side=tkinter.LEFT)

        def close_window():
            if goal_name.get():
                goal_id = self.client.add_goal(Goal(id=-1, name=goal_name.get(), state=False))
                goal = self.client.fetch_goal(goal_id)
                goal.repeat = period.get()
                goal.description = description.get()
                # create future repeat child goals
                self.spawn_repeat_child_goals(goal)
                playsound('data/fx2.wav', block=False)
                self.refresh_goals_canvas()
            top.destroy()
        top.bind('<Return>', lambda _: close_window())
        top.bind('<Escape>', lambda _: top.destroy())
        Button(top, text="Ok", command=close_window).pack()

    class DetailsWindow(GoalDetailsWindow):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.make_daily_button.pack_forget()
            self.backlog_button.pack_forget()
            self.unparent_button.pack_forget()
            self.remove_from_backlog_button.pack_forget()
            self.remove_from_daily_button.pack_forget()

    class RepeatGoalWidget(GoalWidget):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # remove delete button if child goal
            if self.goal.parent != -1:
                self.delete_button.pack_forget()

            # remove checkbox if parent
            if self.goal.parent == -1:
                self.check_button.pack_forget()

    def refresh_goals_canvas(self) -> None:
        # first cleanup the canvas if there are existing goal windows
        self.canvas.delete(tkinter.ALL)
        goals = self.get_all_parent_repeat_goals()
        idx_offset = 0
        for goal in goals:
            def goal_constructor(goal: Goal, x: int, y: int) -> GoalWidget:
                goal_widget = self.RepeatGoalWidget(self.canvas, 
                                         self, 
                                         goal, 
                                         self.client, 
                                         x, 
                                         y, 
                                         details_window_cls=self.DetailsWindow,
                                         disable_dragging=False)
                self.canvas.create_window(0, y, anchor=tkinter.NW, window=goal_widget)
                return goal_widget
            renderer = self.GoalRenderer(goal_constructor, 
                                         self.client.fetch_goal, 
                                         idx_offset,
                                         ignore_children=False)
            idx_offset += renderer.render_goal(goal)