# schwinn_stationary_bike

Reads workout data produced by a Schwinn stationary bike and generates trend charts.

## Python version

This project now targets Python 3.14.

## Quick start

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python read_file.py
```

By default, the app looks for a bike export file at `/Volumes/AARON/AARON1.DAT`.
You can override this with CLI flags:

```bash
python read_file.py --data-file /path/to/AARON1.DAT --history-file Workout_History.csv --days 30
```

## Historical Graphs

![Historical Graphs](/images/HistoricalData.png)

Once the file is read, you will get 2 sets of graphs (you will need to move graph 2 to see graph 1). The 1st set shows you your historical values for:
- Workout Time
- Distance
- Average Speed

## Graphs for last 30 days

![Past 30 Days](/images/Last30days.png)

Once you close those graphs, you will get 2 additional graphs (Graph 2 is on top of graph 1) with the same data for the last 30 days.

## Display of all data

Once you close those graphs, you will see a print out of all your past data points.
