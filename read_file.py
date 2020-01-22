#!/usr/bin/env python

import json
import os
import pandas as pd
import matplotlib.pyplot as plt
import datetime
from pprint import pprint



def is_accessible(path, mode='r'):
    """
    Check if the file or directory at `path` can
    be accessed by the program using `mode` open flags.
    """
    try:
        f = open(path, mode)
        f.close()
    except IOError:
        return False
    return True

def _read_DAT_file():
    '''Setup the file so it can be processed.  By Default, the formatting is all jacked up'''
    os.system("echo '[' > myfile.json")
    os.system("tail -n +9 /Volumes/HATCHER1/AARON1.DAT >> tmp")
    os.system("head -n $((`wc -l <tmp|sed 's/ //g'`-1)) tmp|sed 's/^}/,/' >> myfile.json")
    os.system("echo ']' >> myfile.json")
    os.system("rm tmp")

    with open('myfile.json', 'r') as f:
        workout_json = json.load(f)
    
    #Clean up temp file   
    #os.system("rm myfile.json") 
    return workout_json



def _load_workout_data(workout_json):
    '''Load Workout data from the DAT file from the bike'''
    data = []

    for workout_dict in workout_json:

        workout_date = str(workout_dict['workoutDate']['Month']) + "/" +            str(workout_dict['workoutDate']['Day']) + "/" +            str(workout_dict['workoutDate']['Year'])
        distance = workout_dict['distance']
        speed = workout_dict['averageSpeed']
        time = str(workout_dict['totalWorkoutTime']['Hours']) +             ":" + str(workout_dict['totalWorkoutTime']['Minutes'])
        totalCalories = workout_dict['totalCalories']
        avgHeartRate = workout_dict['avgHeartRate']
        avgRpm = workout_dict['avgRpm']
        avgLevel = workout_dict['avgLevel']

        data.append([workout_date,
                     distance,
                     speed,
                     time, 
                     totalCalories, 
                     avgHeartRate,
                     avgRpm, 
                     avgLevel])
        
        
    cnames = ['Workout Date','Distance','Avg Speed','Workout Time','Total Calories','Heart Rate','RPM','Level']

    df_table = pd.DataFrame(data, columns=cnames)

    return df_table



def _load_history_file(history_file):
    '''Load the file with historical data so we don't lose what the bike purges'''
    try:
        history_df = pd.read_csv(history_file)
    except error as e:
        cnames = ['Workout Date','Distance','Avg Speed','Workout Time','Total Calories','Heart Rate','RPM','Level']
        history_df = pd.DataFrame(columns=cnames, ignore_index=True)
    
    return history_df




def _merge_data(new_file, old_file):
    '''Merge the old data with the new data.  The system only keeps a set number of rows, 
    so older rows will be kept in the historical file and then merged back into the main file'''

    combined_file = new_file.append(old_file)
    unique_df = combined_file.drop_duplicates(subset=None, keep=False)
    
    
    return unique_df



def _write_new_history(data, history_file):
    '''Write the combined file to a new file'''
    data.to_csv(history_file,index=False)




'''Need to create some graphs to display the stuff all nice and purdy like'''
def _graph_progress(df):
    '''ax allows the same axis to be used multiple times for different lines'''

    ax = plt.gca()

    df['Workout Date'] = pd.to_datetime(df['Workout Date'])

    sorted_file = df.sort_values(by='Workout Date', ascending=False)

    sorted_file.plot(kind='line',x='Workout Date',y='Distance', ax=ax)
    sorted_file.plot(kind='line',x='Workout Date',y='Avg Speed', ax=ax)
    sorted_file.plot(kind='line',x='Workout Date',y='Total Calories', ax=ax)
    sorted_file.plot(kind='line',x='Workout Date',y='Heart Rate', ax=ax)

    plt.title('Historical Performance')

    plt.show()
    


def _show_last_30_days(df):
    '''ax allows the same axis to be used multiple times for different lines'''

    ax = plt.gca()

    df['Workout Date'] = pd.to_datetime(df['Workout Date'])
    start_date = datetime.datetime.now() + datetime.timedelta(-30)
    

    last_30_days = df[df['Workout Date'] >= start_date]

    sorted_file = last_30_days.sort_values(by='Workout Date', ascending=False)

    plt.title('Last 30 Days')

    sorted_file.plot(kind='line',x='Workout Date',y='Distance', ax=ax)
    sorted_file.plot(kind='line',x='Workout Date',y='Avg Speed', ax=ax)
    sorted_file.plot(kind='line',x='Workout Date',y='Total Calories', ax=ax)
    sorted_file.plot(kind='line',x='Workout Date',y='Heart Rate', ax=ax)

    plt.show() 
##############################################################
#                   MAIN
##############################################################
if __name__ == "__main__":
    history_file = "Workout_History.csv"
    DATA_FILE = "/Volumes/HATCHER1/AARON1.DAT"
    
    #Load Historical Data
    historical_data = _load_history_file(history_file)
    
    #Check to see if the data file is there. If it's not, just display the historical data.
    check_file = is_accessible(DATA_FILE)
    
    #If the new file is there, Open it and merge it with the Historical File.
    if check_file == True:
        workout_table = _load_workout_data(_read_DAT_file())
    
        combined_data = _merge_data(workout_table, historical_data)
    else:
        combined_data = historical_data
        
    _graph_progress(combined_data)
    _show_last_30_days(combined_data)
    _write_new_history(combined_data, history_file)
    pd.set_option('display.max_rows', combined_data.shape[0]+1)
    pprint(combined_data)
