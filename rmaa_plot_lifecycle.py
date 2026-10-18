"""Dispose pending TkAgg work before the embedding window destroys widgets."""
import tkinter as tk


def cancel_canvas_callbacks(canvas):
    # TkAgg has no public close() for an embedded canvas. These owned after IDs
    # must be cancelled while its Tk widget still exists; otherwise Tk executes
    # an idle callback whose registered Python command was already destroyed.
    for name in ('_idle_draw_id','_event_loop_id'):
        identifier=getattr(canvas,name,None)
        if identifier is not None:
            try:canvas.get_tk_widget().after_cancel(identifier)
            except tk.TclError:pass
            setattr(canvas,name,None)
