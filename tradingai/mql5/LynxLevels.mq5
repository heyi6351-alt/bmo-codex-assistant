//+------------------------------------------------------------------+
//|  LynxLevels.mq5                                                   |
//|  Draws the Python trading bot's live trade levels (entry/SL/TP)   |
//|  natively on the MetaTrader 5 chart.                              |
//|                                                                   |
//|  The bot (mt5_bot.py) writes MQL5\Files\bot_levels_<SYMBOL>.csv   |
//|  each cycle; this indicator reads it on a timer and draws a       |
//|  colour-coded horizontal line for every level of every open trade |
//|  on the current symbol:  ENTRY=blue, SL=red, TP1/2/3=green.       |
//|                                                                   |
//|  INSTALL: copy to  <terminal>\MQL5\Indicators\ , compile in       |
//|  MetaEditor (F7), then drag it onto the XAUUSD / EURUSD chart.    |
//+------------------------------------------------------------------+
#property copyright "tradingaI"
#property version   "1.00"
#property indicator_chart_window
#property indicator_plots 0

input int    RefreshSeconds = 5;      // how often to re-read the levels file
input bool   ShowLabels     = true;   // draw a text tag next to each line
input int    LineWidth      = 1;

string PREFIX = "LYNX_";

//+------------------------------------------------------------------+
int OnInit()
{
   EventSetTimer(RefreshSeconds);
   DrawLevels();
   return(INIT_SUCCEEDED);
}
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0, PREFIX);
}
//+------------------------------------------------------------------+
void OnTimer()
{
   DrawLevels();
}
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated,
                const datetime &time[], const double &open[], const double &high[],
                const double &low[], const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
{
   return(rates_total);
}
//+------------------------------------------------------------------+
color LevelColor(string typ)
{
   if(typ == "ENTRY") return(clrDodgerBlue);
   if(typ == "SL")    return(clrRed);
   if(StringFind(typ, "TP") == 0) return(clrLime);
   return(clrSilver);
}
//+------------------------------------------------------------------+
void DrawLevels()
{
   ObjectsDeleteAll(0, PREFIX);
   string fname = "bot_levels_" + Symbol() + ".csv";
   int h = FileOpen(fname, FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
      return;                                   // bot not running / no file yet

   int idx = 0;
   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      if(StringLen(line) < 3) continue;

      string parts[];
      int n = StringSplit(line, ',', parts);
      if(n < 4) continue;
      if(parts[0] == "type") continue;          // header row

      string typ   = parts[0];
      double price = StringToDouble(parts[1]);
      string label = parts[2];
      if(price <= 0.0) continue;

      string name = PREFIX + IntegerToString(idx++);
      if(ObjectCreate(0, name, OBJ_HLINE, 0, 0, price))
      {
         ObjectSetInteger(0, name, OBJPROP_COLOR, LevelColor(typ));
         ObjectSetInteger(0, name, OBJPROP_STYLE, (typ == "ENTRY") ? STYLE_SOLID : STYLE_DASH);
         ObjectSetInteger(0, name, OBJPROP_WIDTH, LineWidth);
         ObjectSetInteger(0, name, OBJPROP_BACK, true);
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         if(ShowLabels)
            ObjectSetString(0, name, OBJPROP_TEXT, typ + "  " + label);
      }
   }
   FileClose(h);
   ChartRedraw(0);
}
//+------------------------------------------------------------------+
