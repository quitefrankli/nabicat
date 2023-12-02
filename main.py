import tkinter
import yaml
import datetime

from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from tkinter import ttk, Frame, Label, Button, Entry, Scrollbar, Canvas, Checkbutton
from typing import *


class Goal:
    @dataclass
    class Metadata:
        creation_date: datetime
        completion_date: datetime

        def to_dict(self) -> dict:
            return {
                'creation_date': int(self.creation_date.timestamp()) if self.creation_date else 0,
                'completion_date': int(self.completion_date.timestamp()) if self.completion_date else 0
            }
        
    def __init__(self, name: str, state: bool, metadata: Metadata = None):
        self.name = name
        self._completed = tkinter.BooleanVar(value=state)
        if metadata:
            self.metadata = metadata
        else:
            self.metadata = self.Metadata(datetime.now(), None)

    def get_tkinter_var(self) -> tkinter.BooleanVar:
        return self._completed

    def completed(self) -> bool:
        return self._completed.get()
    
    @staticmethod
    def construct_metadata(data: dict) -> Metadata:
        def foo(key: str):
            return datetime.fromtimestamp(data[key]) if data[key] else None
        
        return Goal.Metadata(foo('creation_date'), foo('completion_date'))
    
class App(Frame):
    WIDTH = 500
    HEIGHT = 500
    # GOALS_CANVAS_WIDTH = WIDTH-10
    GOALS_CANVAS_WIDTH = WIDTH * 2/3
    GOAL_ENTRY_HEIGHT = 25
    MAX_GOALS = 30
    SAVE_FILE = f"{Path.home()}/.todoist2_goals.yaml"

    def __init__(self, master=None):
        super().__init__(master)
        self.master.title("Todoist2")
        X = int((self.master.winfo_screenwidth() - self.WIDTH)/2)
        Y = int((self.master.winfo_screenheight() - self.HEIGHT)/2)
        self.master.geometry(f"{self.WIDTH}x{self.HEIGHT}+{X}+{Y}")
        Label(self, text="Goals").pack()

        self.canvas_frame = Frame(self)


        self.scrollbar = Scrollbar(self.canvas_frame)
        self.canvas = Canvas(self.canvas_frame, yscrollcommand=self.scrollbar.set)

        self.goals = self.load_goals()
        self.scrollbar.configure(command=self.canvas.yview)

        self.canvas.configure(scrollregion=(0, 
                                            0, 
                                            self.GOALS_CANVAS_WIDTH, 
                                            self.MAX_GOALS * self.GOAL_ENTRY_HEIGHT))

        self.goal_entry_windows = []
        self.refresh_goals_canvas()
            
        self.canvas.pack(side=tkinter.LEFT)
        self.scrollbar.pack(side=tkinter.LEFT, fill=tkinter.Y)
        self.canvas.config(width=self.GOALS_CANVAS_WIDTH, height=300)

        self.canvas_frame.pack()

        Button(self,
               text="New Goal",
               command=self.spawn_new_goal_creation_window).pack(side=tkinter.BOTTOM)
        self.pack()

    def refresh_goals_canvas(self) -> None:
        # first cleanup the canvas if there are existing goal windows
        self.canvas.delete(tkinter.ALL)

        for i in range(len(self.goals)):
            goal = self.goals[i]
            self.canvas.create_window(0, 
                                      i*self.GOAL_ENTRY_HEIGHT, 
                                      anchor=tkinter.NW,
                                      window=Label(self.canvas, text=goal.name))
            DELETE_BUTTON_OFFSET = 40
            CHECK_BOX_RIGHT_OFFSET = DELETE_BUTTON_OFFSET + self.GOAL_ENTRY_HEIGHT
            self.canvas.create_window(self.GOALS_CANVAS_WIDTH-CHECK_BOX_RIGHT_OFFSET,
                                      i*self.GOAL_ENTRY_HEIGHT,
                                      anchor=tkinter.NW,
                                      window=Checkbutton(self.canvas,
                                                         variable=goal.get_tkinter_var(),
                                                         onvalue=True,
                                                         offvalue=False,
                                                         command=lambda idx=i: self.on_goal_state_change(idx)))
            self.canvas.create_window(self.GOALS_CANVAS_WIDTH-DELETE_BUTTON_OFFSET,
                                      i*self.GOAL_ENTRY_HEIGHT,
                                      anchor=tkinter.NW,
                                      window=Button(self.canvas,
                                                    text='delete',
                                                    command=lambda idx=i: self.delete_goal(idx)))

    def on_goal_state_change(self, idx: int) -> None:
        if self.goals[idx]._completed:
            self.goals[idx].metadata.completion_date = datetime.now()
        else:
            self.goals[idx].metadata.completion_date = None
        self.save_goals()

    def delete_goal(self, idx: int) -> None:
        self.goals.pop(idx)
        self.save_goals()
        self.refresh_goals_canvas()

    def load_goals(self) -> List[Goal]:
        try:
            with open(self.SAVE_FILE, 'r') as file:
                goals = yaml.safe_load(file)
                return [
                    Goal(goal['name'], 
                         goal['state'],
                         Goal.construct_metadata(goal['metadata'])) for goal in goals]
        except FileNotFoundError:
            return []
    
    def save_goals(self) -> None:
        goals = [{
            'name': goal.name,
            'state': goal.completed(),
            'metadata': goal.metadata.to_dict()
        } for goal in self.goals]
        with open(self.SAVE_FILE, 'w') as file:
            yaml.dump(goals, file)

    def spawn_new_goal_creation_window(self):
        top = tkinter.Toplevel(self)
        top.geometry(f"200x200+{self.master.winfo_x()}+{self.master.winfo_y()}")
        top.title("New Goal")
        goal_name = tkinter.StringVar()
        new_goal_entry = Entry(top, textvariable=goal_name)
        new_goal_entry.focus()
        new_goal_entry.pack()

        def close_window():
            if goal_name.get():
                self.goals.append(Goal(goal_name.get(), False))
                self.refresh_goals_canvas()
                self.save_goals()
            top.destroy()
        top.bind('<Return>', lambda _: close_window())
        Button(top, text="Ok", command=close_window).pack()

def main() -> None:
    app = App()
    app.mainloop()

if __name__ == '__main__':
    main()