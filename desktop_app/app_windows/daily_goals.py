import tkinter

from datetime import datetime
from tkinter import ttk, Frame, Label, Button, Entry, Scrollbar, Canvas, Checkbutton, messagebox
from tkinter.scrolledtext import ScrolledText
from typing import *
from playsound import playsound

from desktop_app.goal import Goal
from desktop_app.client import ClientV2
from .goal_widget import GoalWidget
from .goals_window import GoalsWindow


class DailyGoals(GoalsWindow):
    def __init__(self, master: Any, client: ClientV2, refresh_all: Callable):
        super().__init__(master, 'Daily Goals', client, refresh_all)

    def refresh_goals_canvas(self) -> None:
        # first cleanup the canvas if there are existing goal windows
        self.canvas.delete(tkinter.ALL)
        goals = self.client.fetch_goals()
        goals.sort(key=lambda goal: goal.metadata.creation_date.timestamp())
        idx_offset = 0
        today = datetime.now().date()

        for goal in goals:
            if goal.daily.date() != today:
                continue

            def goal_constructor(goal: Goal, x: int, y: int) -> GoalWidget:
                goal_widget = GoalWidget(self.canvas, self, goal, self.client, x, y, disable_dragging=True)
                self.canvas.create_window(0, y, anchor=tkinter.NW, window=goal_widget)
                return goal_widget
            renderer = self.GoalRenderer(goal_constructor, 
                                         self.client.fetch_goal, 
                                         idx_offset,
                                         ignore_children=True)
            idx_offset += renderer.render_goal(goal)

    # def spawn_goal_details_window(self, goal_id: int) -> None:
    #     top = tkinter.Toplevel(self)
    #     top.geometry(f"400x200+{self.master.winfo_rootx()}+{self.master.winfo_rooty()}")
    #     top.title("Goal Details")
    #     goal = self.client.fetch_goal(goal_id)
    #     Label(top, text=f"Goal Name: {goal.name}").pack()
    #     description = ScrolledText(top, wrap=tkinter.WORD, width=40, height=6)
    #     description.pack()
    #     description.insert(tkinter.INSERT, goal.description)
    #     description.focus()
    #     def close_window():
    #         new_description = description.get("1.0", tkinter.END)
    #         if goal.description != new_description:
    #             self.client.need_saving = True
    #             goal.description = new_description
    #         top.destroy()
    #     top.bind('<Escape>', lambda _: top.destroy())
    #     Button(top, text="Ok", command=close_window).pack()
    #     is_daily = goal.daily.date() == datetime.now().date()
    #     def toggle_daily():
    #         self.client.toggle_goal_daily(goal_id)
    #         self.refresh_all()
    #         close_window()
    #     Button(top, 
    #            text="Make Normal" if is_daily else "Make Daily", 
    #            command=toggle_daily).pack(side=tkinter.LEFT)