"""Technical result workspace. One canvas, event-driven updates, no idle polling."""
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg,NavigationToolbar2Tk
from rmaa_i18n import tr
from rmaa_plots import PLOTS,available_plots,curves,draw_plot,TECHNICAL_COLORS
from rmaa_result_model import NAV_NAMES,ORDER,measurement_rows,overview_rows,calibration_rows,display_number
from rmaa_plot_lifecycle import cancel_canvas_callbacks
from rmaa_axis_controls import AxisRangeControls
from rmaa_quality import response_warnings


class ResultWindow(tk.Toplevel):
    def __init__(self,master,results):
        super().__init__(master)
        self.title(tr('RMAA Open Lab — результаты и графики'))
        width=min(1380,self.winfo_screenwidth()-80);height=min(900,self.winfo_screenheight()-150)
        self.geometry(f'{width}x{height}+{max(20,(self.winfo_screenwidth()-width)//2)}+25')
        self.minsize(900,600)
        self.results=[];self.selected_key=None;self._closed=False;self._changing=False
        self._limits={};self._lines=[];self._probe_artists=[]
        self.records=[];self.channels={c:tk.BooleanVar(value=True) for c in ('L','R')}
        self.status=tk.StringVar(value=tr('Щёлкните по графику, чтобы прочитать ближайшие отсчёты.'))
        self.axis_vars=[tk.StringVar() for _ in range(4)]
        self._layout()
        self.set_results(results)
        self.protocol('WM_DELETE_WINDOW',self.destroy)

    def _layout(self):
        style=ttk.Style(self)
        style.configure('Result.Treeview',font=('Consolas',11),rowheight=28)
        style.configure('Result.Treeview.Heading',font=('Segoe UI',9,'bold'))
        style.configure('Result.Nav.Treeview',font=('Segoe UI',10),rowheight=29)
        style.configure('Result.Title.TLabel',font=('Segoe UI',12,'bold'))
        header=ttk.Frame(self,padding=(12,10,12,5));header.pack(fill='x')
        ttk.Label(header,text=tr('Результаты измерения'),style='Result.Title.TLabel').pack(side='left')
        for label,command in [('Открыть JSON / SAV…',self.open_files),('Сохранить JSON…',self.save_json),('HTML-отчёт…',self.save_report),('PNG',self.save_png)]:
            ttk.Button(header,text=tr(label),command=command).pack(side='right',padx=3)
        self.subtitle=ttk.Label(self,padding=(12,0,12,8))
        self.subtitle.pack(fill='x')
        self.quality_message=tk.StringVar()
        self.quality_banner=tk.Label(self,textvariable=self.quality_message,anchor='w',justify='left',
            background='#fff0cf',foreground='#842b00',font=('Segoe UI',10,'bold'),padx=12,pady=8,wraplength=1100)
        outer=ttk.Panedwindow(self,orient='horizontal');outer.pack(fill='both',expand=True,padx=10)
        sidebar=ttk.Frame(outer,width=245);sidebar.pack_propagate(False)
        outer.add(sidebar,weight=0)
        ttk.Label(sidebar,text=tr('Измерения'),padding=(7,5)).pack(anchor='w')
        navframe=ttk.Frame(sidebar);navframe.pack(fill='both',expand=True)
        self.nav=ttk.Treeview(navframe,show='tree',selectmode='browse',style='Result.Nav.Treeview')
        self.nav.column('#0',width=222,minwidth=170,stretch=True)
        scroll=ttk.Scrollbar(navframe,orient='vertical',command=self.nav.yview)
        self.nav.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.nav.pack(fill='both',expand=True)
        self.nav.bind('<<TreeviewSelect>>',self._select)
        self.record_frame=ttk.LabelFrame(sidebar,text=tr('Записи / кривые'),padding=6)
        self.record_frame.pack(fill='x',pady=8)
        channels=ttk.Frame(sidebar);channels.pack(fill='x',pady=(0,8))
        self.channel_buttons={}
        for c in ('L','R'):
            button=ttk.Checkbutton(channels,text=c,variable=self.channels[c],command=self._refresh_visibility)
            button.pack(side='left',padx=8);self.channel_buttons[c]=button
        self.right=ttk.Frame(outer);outer.add(self.right,weight=1)
        self.right.rowconfigure(0,weight=1);self.right.columnconfigure(0,weight=1)
        self.graph_page=ttk.Frame(self.right)
        self.graph_page.rowconfigure(1,weight=1);self.graph_page.columnconfigure(0,weight=1)
        controls=ttk.Frame(self.graph_page,padding=(7,3));controls.grid(row=0,column=0,sticky='ew')
        self.graph_title=ttk.Label(controls,style='Result.Title.TLabel');self.graph_title.pack(side='left')
        ttk.Button(controls,text=tr('Сброс масштаба'),command=self.reset_view).pack(side='right')
        self.split=ttk.Panedwindow(self.graph_page,orient='vertical');self.split.grid(row=1,column=0,sticky='nsew')
        plot=ttk.Frame(self.split,height=450);self.split.add(plot,weight=5)
        self.figure=Figure(figsize=(9,5),dpi=100,facecolor='#141c18')
        self.axes=self.figure.add_subplot(111)
        self.figure.subplots_adjust(left=.09,right=.98,bottom=.13,top=.91)
        self.canvas=FigureCanvasTkAgg(self.figure,master=plot)
        self.toolbar=NavigationToolbar2Tk(self.canvas,plot,pack_toolbar=False)
        self.toolbar.pack(side='bottom',fill='x')
        self.canvas.get_tk_widget().pack(fill='both',expand=True)
        self.chart=(self.canvas,self.toolbar)
        self._mpl_ids=[self.canvas.mpl_connect('motion_notify_event',self._motion),
                       self.canvas.mpl_connect('button_press_event',self._probe),
                       self.canvas.mpl_connect('draw_event',self._sync_ranges)]
        self.axis_controls=AxisRangeControls(self.graph_page,self.axes,self.canvas,self.toolbar)
        self.axis_controls.grid(row=2,column=0,sticky='ew')
        self.axis_vars=self.axis_controls.vars;self.entries=self.axis_controls.entries
        details=ttk.Frame(self.split,height=190);self.split.add(details,weight=1)
        self.details=ttk.Notebook(details);self.details.pack(fill='both',expand=True)
        self.metric_page=ttk.Frame(self.details);self.details.add(self.metric_page,text=tr('Показания'))
        self.metric_table=self._table(self.metric_page)
        self.probe_page=ttk.Frame(self.details);self.details.add(self.probe_page,text=tr('Отсчёт графика'))
        self.probe_table=self._table(self.probe_page)
        self.harmonic_page=ttk.Frame(self.details);self.details.add(self.harmonic_page,text=tr('Гармоники'))
        self.harmonic_table=self._table(self.harmonic_page)
        self.overview_page=ttk.Frame(self.right)
        ttk.Label(self.overview_page,text=tr('Все показатели'),style='Result.Title.TLabel',padding=8).pack(anchor='w')
        self.overview_table=self._table(self.overview_page)
        self.calibration_page=ttk.Frame(self.right)
        ttk.Label(self.calibration_page,text=tr('Калибровка / методика'),style='Result.Title.TLabel',padding=8).pack(anchor='w')
        self.calibration_table=self._table(self.calibration_page)
        self.method= tk.Text(self.calibration_page,height=6,wrap='word',font=('Segoe UI',10),relief='flat',background='#f0f2f3')
        self.method.pack(fill='x',padx=8,pady=8)
        ttk.Label(self,textvariable=self.status,anchor='w',padding=(12,6),wraplength=1200).pack(fill='x')

    def _table(self,parent):
        frame=ttk.Frame(parent);frame.pack(fill='both',expand=True)
        tree=ttk.Treeview(frame,show='tree headings',selectmode='extended',style='Result.Treeview',height=5)
        vs=ttk.Scrollbar(frame,orient='vertical',command=tree.yview)
        hs=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview)
        tree.configure(yscrollcommand=vs.set,xscrollcommand=hs.set)
        vs.pack(side='right',fill='y');hs.pack(side='bottom',fill='x');tree.pack(fill='both',expand=True)
        tree.tag_configure('odd',background='#eef2f4')
        tree.bind('<Control-c>',lambda event:self._copy_rows(tree))
        return tree

    def _fill(self,tree,headers,rows,first=None):
        tree.delete(*tree.get_children())
        columns=[f'c{i}' for i in range(len(headers))];tree.configure(columns=columns)
        tree.heading('#0',text=first or tr('Показатель'))
        tree.column('#0',width=340,minwidth=230,stretch=True)
        for key,label in zip(columns,headers):
            tree.heading(key,text=label);tree.column(key,width=160,minwidth=110,stretch=False,anchor='e')
        for i,(label,values) in enumerate(rows):tree.insert('', 'end',text=label,values=values,tags=('odd',) if i%2 else ())

    def _copy_rows(self,tree):
        rows=[tree.item(i,'text')+'\t'+'\t'.join(map(str,tree.item(i,'values'))) for i in tree.selection()]
        if rows:self.clipboard_clear();self.clipboard_append('\n'.join(rows))
        return 'break'

    def set_results(self,results):
        if not 1<=len(results)<=8:raise ValueError('Select 1..8 result sets')
        self._changing=True
        self.results=list(results);self._limits.clear();self.axis_controls.states.clear();self.selected_key=None
        for child in self.record_frame.winfo_children():child.destroy()
        self.records=[]
        for i,(label,_) in enumerate(self.results):
            var=tk.BooleanVar(value=True);self.records.append(var)
            row=ttk.Frame(self.record_frame);row.pack(fill='x')
            tk.Label(row,text='━',foreground=TECHNICAL_COLORS[i%8],background='#203629',font=('Segoe UI',10,'bold')).pack(side='left')
            ttk.Checkbutton(row,text=f'{i+1}. {label[:25]}',variable=var,command=self._refresh_visibility).pack(side='left')
        has_right=any(data.get('right') for _,data in results)
        self.channels['R'].set(has_right)
        self.channel_buttons['R'].configure(state='normal' if has_right else 'disabled')
        self.channels['L'].set(True)
        rates=', '.join(str(fs) for fs in sorted({data.get('fs',0) for _,data in results}))
        self.subtitle.configure(text=f'{rates} Hz  ·  '+('L / R' if has_right else tr('Моно'))+f'  ·  {len(results)} '+tr('записей'))
        available=set(available_plots(results));self.plot_keys=[key for key in ORDER if key in available]
        self.nav.delete(*self.nav.get_children())
        self.nav.insert('','end',iid='overview',text=tr('Все показатели'))
        for key in self.plot_keys:self.nav.insert('','end',iid=key,text=tr(NAV_NAMES[key]))
        self.nav.insert('','end',iid='calibration',text=tr('Калибровка / методика'))
        key='response' if 'response' in self.plot_keys else self.plot_keys[0] if self.plot_keys else 'overview'
        self._changing=False
        self.nav.selection_set(key);self._show(key)

    def _chosen(self):return [result for i,result in enumerate(self.results) if self.records[i].get()]
    def _channels(self):return tuple(c for c,var in self.channels.items() if var.get())

    def _select(self,event=None):
        if self._changing:return
        selection=self.nav.selection()
        if selection:self._show(selection[0])

    def select_plot(self,key):
        if key not in self.plot_keys:raise ValueError('Unavailable plot')
        self.nav.selection_set(key);self.nav.see(key);self._show(key)

    def _show(self,key):
        if key==self.selected_key:return
        if self.selected_key in self.plot_keys:
            self._limits[self.selected_key]=(self.axes.get_xlim(),self.axes.get_ylim())
            self.axis_controls._remember()
        self.selected_key=key
        for page in (self.graph_page,self.overview_page,self.calibration_page):page.grid_remove()
        if key=='overview':self.overview_page.grid(row=0,column=0,sticky='nsew')
        elif key=='calibration':self.calibration_page.grid(row=0,column=0,sticky='nsew')
        else:
            self.graph_page.grid(row=0,column=0,sticky='nsew')
            self.graph_title.configure(text=PLOTS[key][0])
            draw_plot(self.axes,self.results,key,technical=True)
            self._probe_artists=[];self._lines=[]
            for i,(_,data) in enumerate(self.results):
                self._lines.extend((i,suffix[0]) for suffix,_,_ in curves(data,key))
            self._lines=[(i,c,line) for (i,c),line in zip(self._lines,self.axes.lines)]
            if key in self._limits:self.axes.set_xlim(*self._limits[key][0]);self.axes.set_ylim(*self._limits[key][1])
            self.axis_controls.activate(key,PLOTS[key][3],key in ('response','acoustic_response'))
            self.toolbar.update();self.toolbar.push_current()
            self._fill(self.probe_table,[tr('Частота / время'),PLOTS[key][2]],[],first=tr('Кривая'))
            self.details.select(self.metric_page)
        self._refresh_visibility()

    def _refresh_visibility(self):
        if self._changing:return
        key=self.selected_key;channels=self._channels();chosen=self._chosen()
        warnings=[name+' · '+message for name,data in chosen for message in response_warnings(data,channels)]
        self.quality_message.set('\n'.join(warnings))
        if warnings:self.quality_banner.pack(fill='x',after=self.subtitle,padx=10,pady=(0,6))
        else:self.quality_banner.pack_forget()
        if key in self.plot_keys:
            for i,c,line in self._lines:line.set_visible(self.records[i].get() and c in channels)
            legend=self.axes.get_legend()
            if legend:
                for text,(_,_,line) in zip(legend.get_texts(),self._lines):text.set_alpha(1 if line.get_visible() else .25)
            for artist in self._probe_artists:artist.remove()
            self._probe_artists=[]
            cols,rows=measurement_rows(chosen,key,channels)
            if not rows:rows=[(tr('Числа не сохранены. Используйте отсчёт графика.'),['—']*len(cols))]
            self._fill(self.metric_table,[c[3] for c in cols],rows)
            self._harmonics(chosen,channels)
            self.canvas.draw_idle()
            self.status.set(tr('Щёлкните по графику, чтобы прочитать ближайшие отсчёты.') if any(line.get_visible() for _,_,line in self._lines) else tr('Включите запись и канал для отображения кривых.'))
            if key=='response':
                rejected=[name+': '+data[side]['response'].get('reason','') for name,data in chosen
                          for side in ('left','right') if data.get(side,{}).get('response',{}).get('valid') is False]
                if rejected:self.status.set('АЧХ не прошла проверку: '+'; '.join(rejected))
        elif key=='overview':
            cols,rows=overview_rows(chosen,channels);self._fill(self.overview_table,[c[3] for c in cols],rows)
        else:
            self._fill(self.calibration_table,[name for name,_ in chosen],calibration_rows(chosen))
            self.method.configure(state='normal');self.method.delete('1.0','end')
            for name,data in chosen:
                notes=[str(data.get('algorithm','')),*map(str,data.get('limitations',[]))]
                for side in ('left','right'):
                    response=data.get(side,{}).get('response',{})
                    tracking=response.get('period_clock_tracking',{})
                    notes.extend(str(value) for value in (response.get('method'),tracking.get('assumption'),tracking.get('scope'),response.get('quality_warning')) if value)
                self.method.insert('end',name+'\n'+'\n'.join(notes)+'\n\n')
            self.method.configure(state='disabled')

    def _harmonics(self,chosen,channels):
        rows=[]
        if self.selected_key=='tone':
            for name,data in chosen:
                for side,c in (('left','L'),('right','R')):
                    if c not in channels:continue
                    tone=data.get(side,{}).get('tone',{})
                    for i,(hz,db) in enumerate(zip(tone.get('harmonic_frequencies_hz',[]),tone.get('harmonic_levels_db',[])),2):
                        rows.append((f'{name} · {c} · H{i}',[display_number(hz,'.2f'),display_number(db,'.2f')]))
        self._fill(self.harmonic_table,[tr('Частота, Гц'),'dB / fundamental'],rows,first=tr('Гармоника'))
        self.details.tab(self.harmonic_page,state='normal' if rows else 'disabled')

    def _sync_ranges(self,event=None):
        if not self._closed and self.selected_key in self.plot_keys:self.axis_controls.sync(event)

    def apply_ranges(self):
        if self.selected_key in self.plot_keys:return self.axis_controls.apply()

    def reset_view(self):
        key=self.selected_key
        if key not in self.plot_keys:return
        self._limits.pop(key,None);self.axis_controls.states.pop(key,None)
        self.selected_key=None;self._show(key);self.axis_controls.reset()

    def _motion(self,event):
        if event.inaxes is self.axes and event.xdata is not None and event.ydata is not None:
            text=f'X: {event.xdata:.6g}  ·  Y: {event.ydata:.6g}  ·  '+tr('Щелчок — зафиксировать отсчёт')
            if self.status.get()!=text:self.status.set(text)

    def _probe(self,event):
        if event.inaxes is not self.axes or event.button!=1 or event.xdata is None or self.toolbar.mode:return
        for artist in self._probe_artists:artist.remove()
        self._probe_artists=[self.axes.axvline(event.xdata,color='#b9c5bc',linewidth=.7,linestyle=':')]
        rows=[]
        for _,_,line in self._lines:
            if not line.get_visible():continue
            x=np.asarray(line.get_xdata(),dtype=float);y=np.asarray(line.get_ydata(),dtype=float)
            valid=np.isfinite(x)&np.isfinite(y);x=x[valid];y=y[valid]
            if not len(x):continue
            index=int(np.searchsorted(x,event.xdata));indices=[max(0,index-1),min(len(x)-1,index)]
            index=min(indices,key=lambda i:abs(x[i]-event.xdata))
            marker,=self.axes.plot([x[index]],[y[index]],'o',markersize=4,color=line.get_color())
            self._probe_artists.append(marker)
            rows.append((line.get_label(),[display_number(x[index],'.5g'),display_number(y[index],'.5g')]))
        self._fill(self.probe_table,[tr('Частота, Гц') if PLOTS[self.selected_key][3] else tr('Время / уровень'),PLOTS[self.selected_key][2]],rows,first=tr('Кривая'))
        self.details.select(self.probe_page);self.canvas.draw_idle()
        self.status.set(tr('Ближайшие измеренные отсчёты. Перетаскивание и масштаб — панель под графиком.'))

    def save_png(self):
        if self.selected_key not in self.plot_keys:return
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.png',initialfile=self.selected_key+'.png',filetypes=[('PNG','*.png')])
        if path:
            warning=None
            try:
                if self.quality_message.get():
                    import textwrap
                    warning=self.figure.text(.02,-.03,'\n'.join(textwrap.wrap(self.quality_message.get(),110)),
                        color='#ffb545',fontsize=9,va='top')
                self.figure.savefig(path,dpi=160,facecolor=self.figure.get_facecolor(),bbox_inches='tight')
            except Exception as exc:messagebox.showerror('PNG',str(exc),parent=self)
            finally:
                if warning is not None:warning.remove()

    def save_report(self):
        chosen=self._chosen()
        if not chosen:return
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.html',filetypes=[('HTML','*.html')])
        if path:
            try:
                from rmaa_reports import report_html
                Path(path).write_text(report_html(chosen),encoding='utf8')
            except Exception as exc:messagebox.showerror('HTML',str(exc),parent=self)

    def save_json(self):
        chosen=self._chosen()
        if not chosen:return
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.json',filetypes=[('JSON','*.json')])
        if path:
            data=chosen[0][1] if len(chosen)==1 else {'type':'rmaa-slots','version':1,'slots':chosen+[None]*(8-len(chosen))}
            try:Path(path).write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf8')
            except Exception as exc:messagebox.showerror('JSON',str(exc),parent=self)

    def open_files(self):
        paths=filedialog.askopenfilenames(parent=self,filetypes=[('JSON / SAV','*.json *.sav')])
        if not paths:return
        try:
            from rmaa_reports import load_result_json
            loaded=[]
            for path in paths:
                if len(loaded)>=8:raise ValueError('Select at most 8 result sets')
                if Path(path).suffix.lower()=='.json':
                    if Path(path).stat().st_size>512_000_000:raise ValueError('Session exceeds 512 MB')
                    value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
                    if value.get('type')=='rmaa-slots':
                        slots=value.get('slots')
                        if value.get('version')!=1 or not isinstance(slots,list) or len(slots)!=8:raise ValueError('Invalid session')
                        for slot in slots:
                            if slot is None:continue
                            if not isinstance(slot,list) or len(slot)!=2 or not isinstance(slot[0],str) or not isinstance(slot[1],dict) or not all(k in slot[1] for k in ('left','right')):raise ValueError('Invalid result slot')
                            loaded.append((slot[0],slot[1]))
                    elif 'left' in value and 'right' in value:loaded.append((Path(path).stem,value))
                    else:raise ValueError('Invalid measurement')
                else:loaded.append((Path(path).stem,load_result_json(path)))
            if not 1<=len(loaded)<=8:raise ValueError('Select 1..8 result sets')
            self.set_results(loaded)
        except Exception as exc:messagebox.showerror(tr('Ошибка загрузки'),str(exc),parent=self)

    def destroy(self):
        if self._closed:return
        self._closed=True
        for connection in self._mpl_ids:self.canvas.mpl_disconnect(connection)
        self.figure.clear()
        cancel_canvas_callbacks(self.canvas)
        super().destroy()
