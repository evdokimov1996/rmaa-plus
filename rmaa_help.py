"""Offline, keyboard-accessible operating guide. No browser or network needed."""
import tkinter as tk
from tkinter import ttk
from rmaa_i18n import get_language

RU=[
('1. Перед первым тестом', '''RMAA+ — независимый анализатор звукового тракта для Windows.

Для проверки звуковой карты соедините её линейный выход с линейным входом либо со входом отдельного АЦП. Измеряется вся цепочка: выход, кабель, вход, драйверы и выбранные настройки.

Перед запуском отключите наушники и акустические системы от измеряемого выхода: генератор работает с пиком 0 dBFS. Соединяйте только совместимые линейные разъёмы. Выход усилителя мощности нельзя подключать напрямую к обычному входу АЦП.

Распакуйте ВСЮ папку RMAA+ и запустите RMAA+.exe. Рядом должны оставаться _internal и portable.flag. Установщик и Python для готовой Windows-сборки не нужны. Настройки находятся в data/preferences.json рядом с EXE.

Эта инструкция всегда доступна через «Справка → Как пользоваться» и F1. Слева выберите раздел; стрелки переключают разделы.'''),
('2. Устройства и каналы', '''1. В поле «Воспроизведение» выберите реальный выход звуковой карты. В поле «Запись» — физический вход или отдельный АЦП.
2. Выберите устройства одного Audio API. Для прямой работы с поддерживаемыми устройствами обычно подходит WASAPI Exclusive. ASIO доступен, когда установлен подходящий драйвер и библиотека PortAudio его предоставляет. Выбор пункта сам по себе не устанавливает драйвер.
3. Начните с 48 000 Гц и 24-bit PCM. Оба устройства должны поддерживать выбранный режим. «Пинг» открывает и закрывает его с цифровой тишиной; это не доказательство bit-perfect.
4. Один подключённый вход: выберите «Моно (вход 1)» либо нужный физический номер в «Настройках». В моно программа не выдумывает второй канал и не оценивает взаимопроникновение.
5. Для стерео подключите оба выхода к двум отдельным входам, выберите L/R и проверьте карту каналов.

Отключите эффекты, AGC, шумоподавление и эквалайзеры в измеряемом тракте, если требуется измерить саму аппаратуру. Закройте другие программы, использующие эти устройства. Программа не меняет громкость Windows и усиление оборудования.'''),
('3. Онлайн АЧХ и уровень', '''1. Нажмите «Онлайн АЧХ / уровень», затем «Запустить».
2. Дождитесь сообщения «Сигнал найден» и стабилизации графика.
3. Смотрите на Peak и RMS. Peak показывает максимальные отсчёты, RMS — среднеквадратичный уровень; это разные величины.
4. Генератор выдаёт пик 0 dBFS. Входной уровень задаётся аналоговым трактом/АЦП. Оставьте запас до клиппинга входа; красный индикатор означает, что измерение будет недостоверным.
5. Если сигнал не найден, проверьте кабель, физический канал, вход/выход и режим API. Нельзя исправить неверное подключение масштабированием графика.
6. Остановите монитор перед полным тестом. «Стоп» освобождает принадлежащие ему аудиопотоки.

Онлайн АЧХ нужна для настройки. Итоговая АЧХ и остальные показатели рассчитываются отдельным измерением. Каналы и масштаб можно менять в окне монитора; новые кадры не сбрасывают ручные пределы.'''),
('4. Полное измерение', '''1. Отметьте нужные тесты. В моно взаимопроникновение исключается.
2. При необходимости включите сохранение измеренной WAV и manifest. Выберите новое имя файла: существующие записи не перезаписываются.
3. Нажмите «Начать тестирование» и дождитесь окончания воспроизведения и анализа. Полный точный тест при 48 кГц занимает несколько минут. Во время теста не переключайте устройства и не меняйте усиление.
4. В последовательности есть тишина и тихий пилот −60 dBFS для динамического диапазона. Это специальные участки методики.
5. «Стоп» отменяет текущую работу; неоконченная запись не считается успешным измерением. При отказе анализа предыдущий результат остаётся доступным.

АЧХ измеряется с шагом частоты 1 Гц, двумя фазовыми реализациями и 14 усредняемыми периодами. Основные участки для численных показателей длятся 6 с, CCIF по частоте содержит 64 точки.

Длинные тесты ограничены 20 млн кадров для контроля памяти. При высоких частотах дискретизации выберите меньше измерений либо более низкую Fs. Программа сообщает об ограничении до воспроизведения.'''),
('5. Графики и масштаб', '''После теста открывается техническое окно результатов. Слева выберите АЧХ, шум, гармоники, IMD или другой сохранённый график. Числа находятся под графиком; гармоники — на отдельной вкладке.

Шаг сетки АЧХ по умолчанию — 0,5 дБ. Кнопки ±0,5 / ±1 / ±2 / ±3 задают полный симметричный диапазон Y. Поле «Шаг сетки» задаёт расстояние между основными линиями; пустое значение включает автоматический шаг.

Для ручного ввода задайте X min < X max и Y min < Y max, затем нажмите «Применить» или Enter. Поддерживаются точка, запятая и Ctrl+A при русской/английской раскладке. На логарифмической оси X минимум должен быть положительным. Одинаковые Y min и Y max не образуют диапазон — причина ошибки показана под полями.

Панель под графиком содержит zoom, перемещение, возврат и сохранение изображения. Щелчок по кривой показывает ближайшую измеренную точку. Переключение графиков сохраняет их ручные масштабы; «Сброс масштаба» возвращает исходный вид.

Переключатели записей и L/R позволяют сравнивать кривые. В разделе «8 слотов / отчёты» можно загрузить до восьми наборов и сравнить выбранные.'''),
('6. Как читать показания', '''АЧХ показывается относительно 1 кГц. В таблице есть диапазоны 20 Гц–20 кГц и 40 Гц–15 кГц. Это относительное изменение передачи, а не абсолютный уровень SPL.

THD оценивает выбранные гармоники. THD+N включает остаточный шум и искажения в указанной полосе. IMD относится к конкретным парам частот и выбранным продуктам; сравнивайте результаты при одинаковой методике.

Шум может быть абсолютным dBFS RMS или нормированным к уровню генератора; A-weighted означает частотное взвешивание. RMS и peak, взвешенные и невзвешенные значения нельзя смешивать. Конвенция DR и дополнительные поля сохранены в JSON.

«Калибровка / методика» показывает тактовый дрейф и проверки достоверности. Плавная коррекция времени АЧХ предполагает стабильный тракт; её параметры сохраняются. Резкие скачки или потеря отсчёта отклоняются. Для шумных точек и глубоких провалов есть ограничения достоверности.

Плотная АЧХ — H1/BLA при данном тестовом уровне. Повторяемость и случайная ошибка не равны абсолютной точности. Это независимый проект, не оригинальная RightMark RMAA и не её официальная версия. Точность не хуже RMAA во всех режимах пока не подтверждена.'''),
('7. Сохранение и обмен', '''JSON сохраняет полные собственные результаты и дополнительные проверки. Используйте его для дальнейшего анализа и сравнения.

HTML-отчёт содержит таблицы и графики; его можно распечатать в PDF средствами браузера. PNG сохраняет изображение текущего графика. TXT подходит для численного отчёта.

SAV 6.4.5 используется для обмена поддерживаемыми числами и графиками с RMAA; дополнительные поля Open Lab сохраняйте также в JSON. Импорт SAV не делает независимые методики одинаковыми.

Автономный тест: экспортируйте WAV + manifest, воспроизведите и запишите всю последовательность с обоими маркерами, затем импортируйте запись с тем же manifest. Нужны правильная Fs и соответствующая последовательность.

Программа не загружает аудиозаписи и результаты в интернет. Сетевой запрос выполняется по команде проверки/загрузки обновления. Прежде чем выкладывать собственные записи или JSON, проверьте содержащиеся в них пути и сведения об устройствах.'''),
('8. Обновление и неполадки', '''Обновление: «Справка → Проверить обновления». Программа обращается к публичному GitHub Releases без входа в аккаунт. Если доступна новая Windows x64 версия, можно скачать её и затем установить с перезапуском. Архив проверяется по SHA-256 GitHub, а каждый файл — по описи bundle.json.

Перед перезапуском текущий результат автоматически сохраняется локально в файл update-recovery-*.json. Папка data и посторонние файлы не заменяются. При неудачной проверке новой версии выполняется откат файлов программы; при ошибке доступа копия для восстановления остаётся в каталоге обновления. Не запускайте второй экземпляр программы во время установки.

При внезапном выключении компьютера во время обновления сохраните каталог %LOCALAPPDATA%/RMAAOpenLab/updates. Из его подкаталога bundle можно запустить RMAA+.exe --recover-update с полным путём к соседнему plan.json. Это восстанавливает предыдущие файлы программы из журнала. Подробнее — UPDATES.md в дистрибутиве.

Ручной способ: скачайте ZIP последнего выпуска с GitHub, закройте программу и распакуйте в новую папку. Перенесите свою папку data, сохраните _internal и portable.flag. Не заменяйте только EXE.

Нет сигнала: проверьте соединение и карту каналов. Клиппинг: настройте аналоговый вход. Режим не открывается: проверьте поддержку обоими устройствами и другие аудиопрограммы. АЧХ отклонена по тактам: повторите тест со стабильными устройствами и изучите причину. Ошибка сети не сбрасывает настройки; повторите проверку позже или используйте страницу Releases.''')]

EN=[
('1. Before the first test', '''RMAA+ is an independent Windows audio-path analyzer. Connect a compatible line output to a line input or a separate ADC. The result describes the entire output/cable/input/driver chain.

Disconnect headphones and speakers from the measured output before testing: the generator runs at 0 dBFS sample peak. Never connect a power amplifier output directly to an ordinary ADC input.

Extract the complete RMAA+ folder. Keep RMAA+.exe, _internal and portable.flag together. Python and an installer are not required for the Windows bundle. Portable preferences are in data/preferences.json. Open this guide with Help or F1.'''),
('2. Devices and channels', '''Select the physical playback output and recording input using the same audio host API. WASAPI Exclusive is a useful starting point when both devices support it. ASIO requires a compatible installed driver and an ASIO-capable PortAudio library.

Start with 48 kHz / 24-bit PCM. Ping actually opens and closes the selected format using silence; it does not prove bit transparency.

For one connected input select Mono and the correct physical input number. Stereo requires two separate connected inputs; mono does not produce fabricated stereo balance or crosstalk.

Disable AGC, effects and EQ when measuring the hardware itself. Close other users of these audio devices. RMAA+ does not change Windows volume or hardware gain.'''),
('3. Live response and level', '''Open Live FR / level and press Start. Wait for a stable curve and Signal found.

Peak and RMS are different quantities. The generator stays at 0 dBFS sample peak; adjust the analog input path/ADC and leave headroom below input clipping. A red clipping indication invalidates a measurement.

If no signal is found, check the cable, selected devices, host API and physical channel mapping. Stop the live monitor before the complete test. Its manual axes remain unchanged when new FFT frames arrive. This preliminary curve is for setup; final metrics are measured separately.'''),
('4. Run measurements', '''Select the tests and press Start testing. Optionally save a new measured WAV with its manifest. Existing captures are not overwritten.

A complete precision suite at 48 kHz takes several minutes including analysis. Do not change hardware gain or devices during the test. Silence and the -60 dBFS DR pilot are intentional stages.

Stop cancels the operation; an incomplete recording is not a successful result. Earlier results are retained after failure.

The precision profile uses a 1 Hz response grid, two phase realizations / 14 averaged periods, 6-second scalar stages and 64 CCIF sweep points. Sessions are capped at 20 million frames; use fewer tests or a lower sample rate when necessary.'''),
('5. Graphs and axes', '''Choose a plot on the left. Numeric results and harmonics are below the main graph. Click a curve to inspect the nearest measured sample.

The default FR grid interval is 0.5 dB. The ±0.5 / ±1 / ±2 / ±3 buttons set the symmetric Y range. Grid step is separate; blank means automatic ticks.

Enter X min < X max and Y min < Y max, then press Apply or Enter. Decimal commas and Ctrl+A work with Russian/English keyboard layouts. Logarithmic X requires a positive minimum. Invalid boundaries show a persistent explanation.

The toolbar provides zoom, pan, back and image export. Switching plots preserves their manual ranges; Reset restores the initial view. Record and L/R switches control visible curves. Use the eight-slot workspace to compare result sets.'''),
('6. Interpret the results', '''FR is normalized to 1 kHz; summary bands are 20 Hz–20 kHz and 40 Hz–15 kHz. THD describes harmonics; THD+N also includes residual noise in the stated bandwidth. IMD definitions depend on the carriers and selected products.

Do not mix RMS/peak, weighted/unweighted or normalized/absolute values. JSON retains the definitions and extra fields, including the dynamic-range convention.

Calibration / method shows clock checks. Smooth response timing correction assumes a stable DUT and is explicitly recorded. Abrupt changes and sample slips are rejected. Noisy bins and deep notches have uncertainty limitations.

Dense FR is an H1/BLA at the stated stimulus level. Repeatability is not absolute accuracy. RMAA+ is independent of RightMark; noninferiority to original RMAA across all modes has not been established.'''),
('7. Save and share', '''JSON preserves the complete native result and diagnostics. HTML contains tables and plots and can be printed to PDF by a browser. PNG saves the current graph; TXT provides numeric output.

SAV 6.4.5 exchanges supported values and plots with RMAA; also keep JSON for extra Open Lab fields. Import does not make the methods identical.

Offline testing exports a WAV plus manifest. Record the complete matching sequence including both markers at the correct sample rate, then import it with that manifest.

No audio or results are uploaded. Network access is used when you explicitly check/download an update. Review paths and device information before sharing your own files.'''),
('8. Updates and troubleshooting', '''Choose Help → Check for updates. RMAA+ uses public GitHub Releases without an account. A newer Windows x64 archive is verified against GitHub SHA-256 and its per-file bundle inventory before installation and restart.

The current result is saved locally to update-recovery-*.json before restart. User data and unrelated files are preserved. Failed startup verification rolls back program files; failed rollback leaves a recovery journal. Do not run a second instance while installing.

After a power failure, preserve %LOCALAPPDATA%/RMAAOpenLab/updates. Run the staged bundle/RMAA+.exe with --recover-update and the adjacent plan.json path to restore the previous program files. See UPDATES.md.

For a manual update, extract a complete release into a fresh folder and transfer your data folder. Keep _internal and portable.flag; do not replace the EXE alone.

No signal: check routing and cables. Clipping: adjust the analog input. Unsupported mode: check both devices and other audio applications. Rejected clock checks: inspect the reason and repeat with a stable path. Network failure does not reset preferences; retry later or use the Releases page.''')]

class HelpWindow(tk.Toplevel):
    def __init__(self,parent):
        super().__init__(parent);english=get_language()=='en';self.sections=EN if english else RU
        self.title('RMAA+ — User guide' if english else 'RMAA+ — Как пользоваться')
        self.geometry('980x680');self.minsize(720,480)
        ttk.Label(self,text='User guide • F1 • Offline' if english else 'Инструкция • F1 • Работает без интернета',font=('Segoe UI',13,'bold'),padding=12).pack(anchor='w')
        body=ttk.Panedwindow(self,orient='horizontal');body.pack(fill='both',expand=True,padx=12,pady=6)
        self.nav=tk.Listbox(body,width=30,exportselection=False,font=('Segoe UI',10),activestyle='dotbox');body.add(self.nav,weight=0)
        panel=ttk.Frame(body);body.add(panel,weight=1)
        self.text=tk.Text(panel,wrap='word',font=('Segoe UI',11),padx=16,pady=12,relief='flat',takefocus=True)
        scroll=ttk.Scrollbar(panel,orient='vertical',command=self.text.yview);self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.text.pack(fill='both',expand=True)
        self.text.tag_configure('heading',font=('Segoe UI',15,'bold'),spacing3=16)
        for title,_ in self.sections:self.nav.insert('end',title)
        self.nav.bind('<<ListboxSelect>>',self.show)
        bottom=ttk.Frame(self,padding=10);bottom.pack(fill='x')
        ttk.Button(bottom,text='Previous' if english else 'Назад',command=lambda:self.move(-1)).pack(side='left')
        ttk.Button(bottom,text='Next' if english else 'Далее',command=lambda:self.move(1)).pack(side='left',padx=6)
        ttk.Button(bottom,text='Close' if english else 'Закрыть',command=self.destroy).pack(side='right')
        self.bind('<Escape>',lambda e:self.destroy())
        self.nav.selection_set(0);self.show();self.nav.focus_set()

    def move(self,delta):
        current=self.nav.curselection();index=min(len(self.sections)-1,max(0,(current[0] if current else 0)+delta))
        self.nav.selection_clear(0,'end');self.nav.selection_set(index);self.nav.see(index);self.show()

    def show(self,event=None):
        selected=self.nav.curselection()
        if not selected:return
        title,body=self.sections[selected[0]];self.text.configure(state='normal');self.text.delete('1.0','end')
        self.text.insert('end',title+'\n','heading');self.text.insert('end',body);self.text.configure(state='disabled');self.text.yview_moveto(0)
