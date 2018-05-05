#!/usr/bin/env python
"""
    This module performs the standard "flattening" of the batters
    datasets into a single usable source for model ingestion
"""
from __future__ import division
from datetime import datetime
import pandas as pd
import numpy as np

from util import fetch

# Constant
TODAY = datetime.now().strftime('%Y-%m-%d')


def to_numeric(arr):
    """ Convert values in column to numeric values, and impute
        any missing values with the mean of the column
    """
    arr = pd.to_numeric(
        arr
        .astype(str)
        .str.replace(r'[^\d.]+', '')
    )
    avg = arr.mean()
    return arr.fillna(avg)


def flatten_batters(data):
    """ Main execution of this script, the code
        isn't very clean or structured but this just needs
        to work for now.
    """
    #######################################################
    # Extract and format data from dictionay

    # Daily fantasy where row type is Hitter
    dfs = data['dfs'].query('p_h == "H"')

    # Player link for batters
    player_link = data['player_link'].astype({'fg_id': str})

    # Player link for pitchers
    player_link_pl = (
        data['player_link']
        .astype({'fg_id': str})
        .add_suffix('_pl')
    )

    # Team link
    team_link = data['team_link']

    # Fangraphs batters - current stats
    fg_batters = (
        data['fg_batters']
        .astype({'fg_id': str})
        .add_suffix('_b')
    )

    # Fangraphs batters vs oppt pitching hand
    fg_batters_hand = data['fg_batters_lhp'].assign(oppt_pitch_hand='L')
    tmp = data['fg_batters_rhp'].assign(oppt_pitch_hand='R')
    fg_batters_hand = (
        fg_batters_hand
        .append(tmp)
        .astype({'fg_id': str})
        .add_suffix('_bh')
    )

    # Fangraphs batters home vs away
    fg_batters_ha = data['fg_batters_home'].assign(h_a='H')
    tmp = data['fg_batters_away'].assign(h_a='A')
    fg_batters_ha = (
        fg_batters_ha
        .append(tmp)
        .astype({'fg_id': str})
        .add_suffix('_ha')
    )

    # Statcast batters
    statcast_batters = data['statcast_batters'].add_suffix('_sc')

    # Park factors
    park_factor = data['park_factor'].add_suffix('_pf')

    # Fangraphs Pitchers vs oppt batting hand
    fg_pitchers_hand = data['fg_pitchers_lhb'].assign(oppt_bat_hand='L')
    tmp = data['fg_pitchers_rhb'].assign(oppt_bat_hand='R')
    fg_pitchers_hand = (
        fg_pitchers_hand
        .append(tmp)
        .astype({'fg_id': str})
        .add_suffix('_ph')
    )

    # Fangraphs batters daily stats
    fg_batters_daily = (
        data['fg_batters_daily']
        .astype({'fg_id': str})
        .add_suffix('_bd')
    )

    # Today's weather
    weather_today = data['weather_today'].add_suffix('_wt')


    #######################################################
    # Merge tables together

    df = (
        # Use dfs as base table
        dfs

        # Link to player ids for batter
        .merge(player_link, on='mlb_id', how='left')

        # Link to player ids for oppt pitcher
        .merge(player_link_pl, left_on='oppt_pitch_mlb_id', right_on='mlb_id_pl', how='left')

        # We only use team for park factors/weather, so find the "team" for where the game is being played
        .assign(team_guru=lambda x: np.where(
            x['h_a'] == 'h',
            x['team'],
            x['oppt']
        ))

        # Link to team mapping for park factors
        .merge(team_link, on='team_guru', how='left')

        # Base batter stats
        .merge(fg_batters, left_on='fg_id', right_on='fg_id_b', how='left')

        # Batter stats vs oppt pitch hand
        .merge(fg_batters_hand, left_on=['fg_id', 'oppt_pitch_hand'],
               right_on=['fg_id_bh', 'oppt_pitch_hand_bh'], how='left')

        # Batter stats for home vs away
        .assign(h_a=lambda x: x['h_a'].str.upper())
        .merge(fg_batters_ha, left_on=['fg_id', 'h_a'],
               right_on=['fg_id_ha', 'h_a_ha'], how='left')

        # Statcast batter stats
        .merge(statcast_batters, left_on='mlb_id', right_on='mlb_id_sc', how='left')

        # Adjust "hand" column to account for switch hitters
        .assign(hand=lambda x: np.where(
            (x['hand'] == 'B') & (x['oppt_pitch_hand'] == 'L'),
            'R',
            np.where(
                (x['hand'] == 'B') & (x['oppt_pitch_hand'] == 'R'),
                'L',
                x['hand']
            )
        ))

        # Park factors by batter hand
        .merge(park_factor, left_on='team_park', right_on='team_pf', how='left')
        .assign(side=lambda x: x['side'].str[:1])
        .query('side == hand')

        # Get pitcher stats vs batters of the same hand
        .merge(fg_pitchers_hand, left_on=['fg_id_pl', 'hand'],
               right_on=['fg_id_ph', 'oppt_bat_hand_ph'], how='left')

        # Daily stats for model training targets
        .merge(fg_batters_daily, left_on='fg_id', right_on='fg_id_bd', how='left')

        # Parse date columns
        .assign(game_date=lambda x:
            pd.to_datetime(x['game_date'].astype(str), format="%Y%m%d").dt.strftime('%Y-%m-%d')
        )
        .assign(game_date_bd=lambda x:
            pd.to_datetime(x['game_date_bd'], format='%Y-%m-%d').dt.strftime('%Y-%m-%d')
        )

        # Only rows where game_date and game_date_bd match
        .query('game_date == game_date_bd')

        # Merge on today's weather
        .merge(weather_today, left_on='team_weather', right_on='team_weather_wt', how='left')
    )
    logging.info("Datasets merged, rows: %d", df.shape[0])

    
    #######################################################
    # Feature filtering, cleaning, and imputing

    # Assign weather columns for date < today and date == today
    for col in ['w_speed', 'w_dir', 'temp']:
        df[col] = np.where(
            df['game_date'] == TODAY,
            df[col + '_wt'],
            df[col]
        )

    # List of feature columns we are interested in keeping
    all_features = [
        'age_b', 'bb_perc_b', 'k_perc_b', 'bb_k_b', 'obp_b', 'ld_perc_b', 'gb_perc_b', 'fb_perc_b',
        'iffb_perc_b', 'ifh_perc_b', 'buh_perc_b', 'woba_b', 'wraa_b', 'wrc_b', 'spd_b',
        'wrc_plus_b', 'wpa_b', 'o_swing_perc_b', 'z_swing_perc_b', 'swing_perc_b',
        'o_contact_perc_b', 'z_contact_perc_b', 'contact_perc_b', 'zone_perc_b', 'f_strike_perc_b',
        'swstr_perc_b', 'bsr_b', 'pull_perc_b', 'cent_perc_b', 'oppo_perc_b', 'soft_perc_b', 
        'med_perc_b', 'hard_perc_b', 'bb_perc_bh', 'k_perc_bh', 'bb_k_bh', 'obp_bh', 'w_rc_bh',
        'w_raa_bh', 'w_oba_bh', 'wrc_plus_bh', 'ld_perc_bh', 'gb_perc_bh', 'fb_perc_bh',
        'iffb_perc_bh', 'ifh_perc_bh', 'buh_perc_bh', 'pull_perc_bh', 'cent_perc_bh',
        'oppo_perc_bh', 'soft_perc_bh', 'med_perc_bh', 'hard_perc_bh', 'bb_perc_ha', 'k_perc_ha',
        'bb_k_ha', 'obp_ha', 'w_rc_ha', 'w_raa_ha', 'w_oba_ha', 'wrc_plus_ha', 'ld_perc_ha',
        'gb_perc_ha', 'fb_perc_ha', 'iffb_perc_ha', 'ifh_perc_ha', 'buh_perc_ha', 'pull_perc_ha',
        'cent_perc_ha', 'oppo_perc_ha', 'soft_perc_ha', 'med_perc_ha', 'hard_perc_ha',
        'max_hit_speed_sc', 'avg_hit_speed_sc', 'fbld_sc', 'gb_sc', 'max_distance_sc',
        'avg_distance_sc', 'avg_hr_distance_sc', 'barrels_sc', 'brl_percent_sc', 'brl_pa_sc',
        'ev95plus_sc', 'ev95percent_sc', 'fb_factor_pf', 'gb_factor_pf', 'ld_factor_pf',
        'pu_factor_pf', 'factor_1b_pf', 'factor_2b_pf', 'factor_3b_pf', 'hr_factor_pf',
        'runs_factor_pf', 'obp_ph', 'w_oba_ph', 'k_9_ph', 'bb_9_ph', 'k_bb_ph', 'hr_9_ph',
        'k_perc_ph', 'bb_perc_ph', 'k_bb_perc_ph', 'whip_ph', 'x_fip_ph', 'fip_ph', 'ld_perc_ph',
        'gb_perc_ph', 'fb_perc_ph', 'iffb_perc_ph', 'ifh_perc_ph', 'buh_perc_ph', 'pull_perc_ph',
        'cent_perc_ph', 'oppo_perc_ph', 'soft_perc_ph', 'med_perc_ph', 'hard_perc_ph', 'temp',
        'w_speed', 'w_dir', 'prior_adi'
    ]

    target_cols = ['one_b', 'two_b', 'three_b', 'hr', 'rbi', 'r', 'bb', 'hbp', 'sb', 'dk_points', 'fd_points']
    id_cols =['name_first_last', 'team', 'game_date', 'dk_pos', 'fd_pos', 'dk_salary', 'fd_salary']

    # Remove uninteresting columns
    df = df[all_features + target_cols + id_cols]

    # Clean all feature columns
    for col in all_features:
        df[col] = to_numeric(df[col])

    # Split into training and validation data
    train = df[df['game_date'] != TODAY]
    valid = df[df['game_date'] == TODAY]
    del df

    # Clean regression targets for training data, remove from valid
    for col in target_cols:
        train[col] = to_numeric(train[col])
        valid.drop(col, 1)

    return train, valid
























    

