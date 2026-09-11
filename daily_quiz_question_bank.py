"""BETROXY rotating daily sports quiz question bank.

The bank is intentionally deterministic and self-contained:
- 280 questions total (40 per weekday theme)
- each theme has 10 easy, 18 medium, 12 hard
- selection is 2 easy, 3 medium, 2 hard
- question order stays easy -> medium -> hard
- caller supplies question texts used in the previous 30 days
"""
from __future__ import annotations

import hashlib
import random
from datetime import date

THEMES = {
    0: "Cricket Mania",
    1: "Football Fever",
    2: "Mixed Sports",
    3: "Legends & Records",
    4: "Hard Mode",
    5: "India Sports Special",
    6: "Championship Mix",
}

QUESTION_BANK = []


def _add(theme, difficulty, sport, question, options, correct):
    item = {
        "theme": str(theme),
        "difficulty": int(difficulty),
        "sport": str(sport),
        "question": str(question),
        "options": [str(x) for x in options],
        "correct": int(correct),
    }
    QUESTION_BANK.append(item)


def _num_options(answer, steps=(1, 2, 3)):
    answer = int(answer)
    vals = [answer, answer + steps[0], max(0, answer - steps[1]), answer + steps[2]]
    seen = {answer}
    out = [answer]
    bump = 1
    for value in vals[1:]:
        v = int(value)
        while v in seen:
            v = answer + max(1, bump)
            bump += 1
        seen.add(v)
        out.append(v)
    return [str(v) for v in out]


EASY = {
    "Cricket Mania": [
        ("Cricket", "How many legal balls are there in a standard cricket over?", ["5", "6", "7", "8"], 1),
        ("Cricket", "How many runs is a boundary worth when the ball reaches the rope after touching the ground?", ["3", "4", "5", "6"], 1),
        ("Cricket", "How many runs is a clean hit over the boundary rope worth?", ["4", "5", "6", "8"], 2),
        ("Cricket", "What does LBW stand for?", ["Leg Before Wicket", "Line Behind Wicket", "Long Ball Wide", "Leg By Wicket"], 0),
        ("Cricket", "Which player is allowed to wear wicketkeeping gloves?", ["Captain", "Bowler", "Wicketkeeper", "Any fielder"], 2),
        ("Cricket", "How many overs are scheduled per side in a standard ODI?", ["20", "40", "50", "60"], 2),
        ("Cricket", "How many overs are scheduled per side in a standard T20 match?", ["10", "20", "40", "50"], 1),
        ("Cricket", "A cricket hat-trick means a bowler takes how many wickets in three consecutive legal deliveries?", ["2", "3", "4", "5"], 1),
        ("Cricket", "How many runs does a batter need for a century?", ["50", "75", "100", "150"], 2),
        ("Cricket", "After winning the toss, a captain normally chooses whether to bat or do what?", ["Field", "Change teams", "End the match", "Skip an innings"], 0),
    ],
    "Football Fever": [
        ("Football", "How many players does one team normally start with on the pitch?", ["9", "10", "11", "12"], 2),
        ("Football", "Which card sends a player off the field?", ["Blue", "Green", "Yellow", "Red"], 3),
        ("Football", "How many minutes are in normal regulation time, excluding added time?", ["80", "90", "100", "120"], 1),
        ("Football", "A football hat-trick means one player scores how many goals?", ["2", "3", "4", "5"], 1),
        ("Football", "How many points is one goal worth on the scoreboard?", ["1", "2", "3", "6"], 0),
        ("Football", "Which player may handle the ball with the hands inside their own penalty area?", ["Any defender", "Goalkeeper", "Captain only", "Striker"], 1),
        ("Football", "A standard match begins each half with what restart?", ["Corner kick", "Throw-in", "Kick-off", "Penalty kick"], 2),
        ("Football", "What color card is normally used for a caution?", ["Yellow", "Red", "Blue", "White"], 0),
        ("Football", "In a standard penalty shootout, how many initial kicks does each team take before sudden-death rules may apply?", ["3", "4", "5", "6"], 2),
        ("Football", "If the defending team last touches the ball before it crosses its own goal line without a goal, what restart can the attacking team receive?", ["Corner kick", "Kick-off", "Drop ball", "Indirect kick from halfway"], 0),
    ],
    "Mixed Sports": [
        ("Tennis", "In tennis scoring, what does 'love' mean?", ["0", "10", "15", "30"], 0),
        ("Basketball", "How many points is a successful free throw worth?", ["1", "2", "3", "4"], 0),
        ("Badminton", "A standard badminton game is normally played to how many points?", ["15", "21", "25", "30"], 1),
        ("Volleyball", "How many players from one indoor volleyball team are on court at a time?", ["5", "6", "7", "8"], 1),
        ("Field Hockey", "How many players does one field-hockey team normally have on the field?", ["9", "10", "11", "12"], 2),
        ("Baseball", "How many innings are scheduled in a standard Major League Baseball game?", ["7", "8", "9", "10"], 2),
        ("Golf", "In stroke-play golf, which total score is better?", ["Higher score", "Lower score", "Only even scores", "Scores do not matter"], 1),
        ("Table Tennis", "A standard table-tennis game is normally played to how many points?", ["9", "11", "15", "21"], 1),
        ("Olympics", "How many rings are in the Olympic symbol?", ["4", "5", "6", "7"], 1),
        ("Basketball", "A shot made from beyond the three-point line is worth how many points?", ["1", "2", "3", "4"], 2),
    ],
    "Legends & Records": [
        ("Football", "Which country won the first men's FIFA World Cup in 1930?", ["Argentina", "Brazil", "Italy", "Uruguay"], 3),
        ("Cricket", "In which year was the first men's Cricket World Cup held?", ["1971", "1975", "1979", "1983"], 1),
        ("Cricket", "Who captained India to the 1983 men's Cricket World Cup title?", ["Sunil Gavaskar", "Kapil Dev", "Mohinder Amarnath", "Dilip Vengsarkar"], 1),
        ("Cricket", "At which Mumbai ground was the 2011 men's Cricket World Cup final played?", ["Brabourne Stadium", "Wankhede Stadium", "DY Patil Stadium", "Eden Gardens"], 1),
        ("Football", "Which country hosted and won the 1966 men's FIFA World Cup?", ["England", "Germany", "Italy", "Spain"], 0),
        ("Football", "Who scored the famous 'Hand of God' goal at the 1986 men's FIFA World Cup?", ["Pelé", "Diego Maradona", "Johan Cruyff", "Zinedine Zidane"], 1),
        ("Tennis", "Which Grand Slam is played on grass at the All England Club?", ["Australian Open", "French Open", "US Open", "Wimbledon"], 3),
        ("Athletics", "Which event made Usain Bolt famous as a sprint legend?", ["100 metres", "Marathon", "High jump", "Discus"], 0),
        ("Basketball", "Which league's championship series is called the NBA Finals?", ["Baseball", "Basketball", "Ice hockey", "American football"], 1),
        ("Golf", "The Masters Tournament is traditionally played at which course?", ["Augusta National", "St Andrews Old Course", "Pebble Beach", "Pinehurst No. 2"], 0),
    ],
    "Hard Mode": [
        ("Cricket", "If a batter is out without scoring, what is that score commonly called?", ["Duck", "Maiden", "Bye", "Dot over"], 0),
        ("Football", "What is awarded when a direct-free-kick offence by a defender occurs inside that defender's penalty area?", ["Corner kick", "Penalty kick", "Throw-in", "Kick-off"], 1),
        ("Cricket", "A maiden over is an over in which the bowler concedes how many runs from the bat or extras charged to the bowler?", ["0", "1", "2", "6"], 0),
        ("Football", "A throw-in is awarded when the whole ball crosses which boundary?", ["Goal line only", "Touchline", "Penalty arc", "Halfway line"], 1),
        ("Tennis", "At 40-40 in standard tennis scoring, what is the score called?", ["Deuce", "Love", "Set point", "Break"], 0),
        ("Cricket", "Which dismissal can occur when the wicketkeeper breaks the wicket while the striker is out of the crease and not attempting a run?", ["Stumped", "Run out only", "Hit wicket", "Timed out"], 0),
        ("Football", "Can a player be offside directly from a throw-in under the Laws of the Game?", ["Yes, always", "No", "Only in added time", "Only the goalkeeper"], 1),
        ("Cricket", "Which extra is recorded when a legal delivery passes the batter and wicketkeeper without touching the bat or body and runs are completed?", ["Bye", "Leg bye", "No-ball", "Penalty run"], 0),
        ("Football", "How far is the penalty mark from the goal line?", ["10 yards", "11 yards", "12 yards", "15 yards"], 2),
        ("Cricket", "What is the maximum number of fielders normally allowed outside the 30-yard circle during the final 10 overs of a men's ODI under standard current fielding restrictions?", ["3", "4", "5", "6"], 2),
    ],
    "India Sports Special": [
        ("India Sports", "Does India have an officially declared national sport?", ["Yes, hockey", "Yes, cricket", "No", "Yes, kabaddi"], 2),
        ("Athletics", "Neeraj Chopra is best known for which event?", ["Javelin throw", "Shot put", "Long jump", "400 metres"], 0),
        ("Badminton", "PV Sindhu competes in which sport?", ["Tennis", "Badminton", "Squash", "Table tennis"], 1),
        ("Badminton", "Saina Nehwal is associated with which sport?", ["Badminton", "Boxing", "Shooting", "Wrestling"], 0),
        ("Chess", "Viswanathan Anand is famous in which sport?", ["Chess", "Golf", "Archery", "Hockey"], 0),
        ("Boxing", "Mary Kom is famous for which sport?", ["Boxing", "Wrestling", "Weightlifting", "Fencing"], 0),
        ("Field Hockey", "Major Dhyan Chand is a legendary Indian name in which sport?", ["Football", "Field hockey", "Cricket", "Tennis"], 1),
        ("Cricket", "Sachin Tendulkar is associated with which sport?", ["Cricket", "Hockey", "Football", "Badminton"], 0),
        ("Football", "Sunil Chhetri is associated with which sport?", ["Basketball", "Football", "Volleyball", "Kabaddi"], 1),
        ("Shooting", "Abhinav Bindra won India's first individual Olympic gold medal in which sport?", ["Shooting", "Boxing", "Wrestling", "Badminton"], 0),
    ],
    "Championship Mix": [
        ("Cricket", "In which year was the first IPL season played?", ["2007", "2008", "2009", "2010"], 1),
        ("Cricket", "Which team won the inaugural IPL title in 2008?", ["Chennai Super Kings", "Mumbai Indians", "Rajasthan Royals", "Kolkata Knight Riders"], 2),
        ("Football", "The UEFA Champions League is a major club competition in which sport?", ["Football", "Basketball", "Cricket", "Rugby"], 0),
        ("Football", "The men's FIFA World Cup is normally held how often?", ["Every 2 years", "Every 3 years", "Every 4 years", "Every 5 years"], 2),
        ("Cricket", "The men's ODI Cricket World Cup uses what basic innings format?", ["10 overs per side", "20 overs per side", "50 overs per side", "Unlimited overs"], 2),
        ("Tennis", "Wimbledon is a championship in which sport?", ["Tennis", "Golf", "Badminton", "Squash"], 0),
        ("American Football", "The Super Bowl decides the champion of which league?", ["NBA", "NFL", "MLB", "NHL"], 1),
        ("Ice Hockey", "The Stanley Cup is awarded in which sport?", ["Ice hockey", "Baseball", "Basketball", "Cricket"], 0),
        ("Basketball", "The NBA Finals decide the champion in which sport?", ["Basketball", "Football", "Tennis", "Rugby"], 0),
        ("Tennis", "The Davis Cup is an international team competition in which sport?", ["Tennis", "Golf", "Badminton", "Table tennis"], 0),
    ],
}

for theme, rows in EASY.items():
    for sport, text, opts, correct in rows:
        _add(theme, 1, sport, text, opts, correct)


def _add_cricket_math():
    theme = "Cricket Mania"
    for a,b,c in [(2,4,6),(1,3,4),(6,6,1),(4,4,2),(3,2,5),(6,2,4)]:
        _add(theme, 2, "Cricket", f"A batter scores {a}, {b} and {c} from three deliveries. What is the total?", _num_options(a+b+c), 0)
    for overs in (7, 9, 12, 14):
        _add(theme, 2, "Cricket", f"How many legal balls are in {overs} complete overs?", _num_options(overs*6, (6, 12, 18)), 0)
    for target,current in ((181,143),(251,214),(161,127),(301,268)):
        _add(theme, 2, "Cricket", f"A team needs {target} to win and is on {current}. How many more runs are required?", _num_options(target-current), 0)
    for runs,balls in ((36,24),(45,30),(54,36),(72,48)):
        _add(theme, 2, "Cricket", f"A batter scores {runs} runs from {balls} balls. What is the strike rate?", _num_options(int(runs*100/balls), (5, 10, 15)), 0)
    for target,current,runs in [(220,154,8),(196,131,5),(175,119,9),(260,188,12),(150,97,7),(210,146,10)]:
        _add(theme, 3, "Cricket", f"A chase target is {target}. The batting side is {current}, then scores {runs} runs in the next over. How many more runs are then needed to win?", _num_options(target-(current+runs)), 0)
    for runs,overs in ((42,6),(56,8),(63,9)):
        _add(theme, 3, "Cricket", f"A bowler concedes {runs} runs in {overs} overs. What is the economy rate in runs per over?", _num_options(runs//overs), 0)
    for need,overs in ((72,8),(84,7),(96,8)):
        _add(theme, 3, "Cricket", f"A team needs {need} runs from {overs} overs. What required run rate per over is needed if the rate is constant?", _num_options(need//overs), 0)


def _add_football_math():
    theme = "Football Fever"
    for w,d in ((2,1),(3,2),(4,1),(5,0),(1,3),(2,4)):
        _add(theme, 2, "Football", f"A team records {w} wins and {d} draws. Using 3 points for a win and 1 for a draw, how many points is that?", _num_options(3*w+d), 0)
    for gf,ga in ((12,7),(18,11),(15,9),(21,14),(10,6),(16,8)):
        _add(theme, 2, "Football", f"A team has scored {gf} goals and conceded {ga}. What is its goal difference?", _num_options(gf-ga), 0)
    for home,away,new_home in [(1,0,2),(2,1,1),(0,0,3),(3,2,2),(1,1,2),(2,0,1)]:
        _add(theme, 2, "Football", f"The score is {home}-{away}. The home team then scores {new_home} more goal(s). What is the home team's final goal total?", _num_options(home+new_home), 0)
    for w1,d1,w2,d2 in [(3,1,1,2),(2,2,2,1),(4,0,1,3),(1,3,3,0),(2,1,2,2),(3,2,0,1)]:
        _add(theme, 3, "Football", f"Across two blocks of matches a team gets {w1} wins and {d1} draws, then {w2} wins and {d2} draws. At 3 points per win and 1 per draw, what is the total?", _num_options(3*(w1+w2)+(d1+d2)), 0)
    for a1,b1,a2,b2 in [(2,1,1,1),(1,0,2,2),(3,1,0,2),(2,2,2,1),(1,2,3,0),(0,1,2,0)]:
        _add(theme, 3, "Football", f"In a two-leg tie, Team A scores {a1} in leg one and {a2} in leg two. How many aggregate goals did Team A score?", _num_options(a1+a2), 0)


def _add_mixed_math():
    theme = "Mixed Sports"
    for twos,threes,fts in ((5,2,3),(6,1,4),(4,3,2),(7,2,1),(3,4,5),(8,1,2)):
        _add(theme, 2, "Basketball", f"A player makes {twos} two-point shots, {threes} three-pointers and {fts} free throws. How many points is that?", _num_options(2*twos+3*threes+fts), 0)
    for a,b,c in ((4,6,1),(2,3,4),(6,1,2),(3,4,4),(1,6,6),(2,4,5)):
        _add(theme, 2, "Cricket", f"In cricket, a batter scores {a}, {b} and {c} on three scoring shots. What is the total?", _num_options(a+b+c), 0)
    for w,d in ((1,2),(2,2),(3,1),(2,3),(4,0),(1,4)):
        _add(theme, 2, "Football", f"A football team has {w} wins and {d} draws. At 3 points per win and 1 per draw, what is the points total?", _num_options(3*w+d), 0)
    hard = [
        ("Basketball", 4*2 + 3*3 + 5, "A basketball player makes 4 two-pointers, 3 three-pointers and 5 free throws. What is the total score?"),
        ("Cricket", 6+4+3+2+1, "A cricket batter scores 6, 4, 3, 2 and 1 on five scoring shots. What is the total?"),
        ("Football", 3*3+2, "A football team has 3 wins, 2 draws and 1 loss. How many league points does it have at 3 for a win and 1 for a draw?"),
        ("Basketball", 7*2 + 2*3 + 4, "A basketball player makes 7 two-pointers, 2 three-pointers and 4 free throws. What is the total?"),
        ("Cricket", 84//7, "A cricket team needs 84 runs from 7 overs. What constant required run rate per over is that?"),
        ("Football", 3*(2+3)+(1+1), "Across two football phases a team records 2 wins and 1 draw, then 3 wins and 1 draw. What is the combined points total?"),
        ("Basketball", 5*2 + 5*3 + 2, "A basketball player makes 5 two-pointers, 5 three-pointers and 2 free throws. What is the total?"),
        ("Cricket", 48//6, "A cricket bowler concedes 48 runs in 6 overs. What is the economy rate?"),
        ("Football", 17-9, "A football side scores 17 and concedes 9 across a tournament. What is its goal difference?"),
        ("Basketball", 9*2 + 1*3 + 6, "A basketball player makes 9 two-pointers, 1 three-pointer and 6 free throws. What is the total?"),
        ("Cricket", 240-191, "A cricket team is chasing 240 and is on 191. How many more runs are needed to win?"),
        ("Football", 3*4+3, "A football team records 4 wins and 3 draws. What is its points total?"),
    ]
    for sport, ans, text in hard:
        _add(theme, 3, sport, text, _num_options(ans), 0)


def _add_legends_math():
    theme = "Legends & Records"
    pairs = [
        ("the first men's FIFA World Cup",1930,"England's men's FIFA World Cup win",1966),("the first men's Cricket World Cup",1975,"India's first men's Cricket World Cup title",1983),
        ("India's 1983 men's Cricket World Cup title",1983,"India's 2011 men's Cricket World Cup title",2011),("Argentina's 1978 men's FIFA World Cup title",1978,"Argentina's 1986 men's FIFA World Cup title",1986),
        ("Brazil's 1958 men's FIFA World Cup title",1958,"Brazil's 1970 title",1970),("the 1966 men's FIFA World Cup",1966,"the 1990 men's FIFA World Cup",1990),
        ("the first IPL season",2008,"India's 2011 men's Cricket World Cup title",2011),("the 1975 men's Cricket World Cup",1975,"the 1996 men's Cricket World Cup",1996),
        ("the 1986 men's FIFA World Cup",1986,"the 2010 men's FIFA World Cup",2010),("the 1992 men's Cricket World Cup",1992,"the 2011 men's Cricket World Cup",2011),
        ("the 2002 men's FIFA World Cup",2002,"the 2014 men's FIFA World Cup",2014),("the 2003 men's Cricket World Cup",2003,"the 2019 men's Cricket World Cup",2019),
        ("the 1998 men's FIFA World Cup",1998,"the 2018 men's FIFA World Cup",2018),("the 1987 men's Cricket World Cup",1987,"the 2007 men's Cricket World Cup",2007),
        ("the 1970 men's FIFA World Cup",1970,"the 1994 men's FIFA World Cup",1994),("the 1999 men's Cricket World Cup",1999,"the 2015 men's Cricket World Cup",2015),
        ("the 1954 men's FIFA World Cup",1954,"the 1974 men's FIFA World Cup",1974),("the 1979 men's Cricket World Cup",1979,"the 1999 men's Cricket World Cup",1999),
    ]
    for a,ya,b,yb in pairs:
        _add(theme, 2, "Sports History", f"{a.title()} was in {ya}, while {b} was in {yb}. How many years apart were they?", _num_options(yb-ya, (4, 8, 12)), 0)
    for y1,y2,y3,label in [(1975,1983,2011,"Cricket World Cup milestones"),(1930,1966,2010,"FIFA World Cup milestones"),(1958,1970,1994,"Brazil World Cup milestones"),(1978,1986,2022,"Argentina World Cup milestones"),(1987,1999,2011,"Cricket World Cup milestones"),(1992,2003,2019,"Cricket World Cup milestones"),(1966,1990,2018,"FIFA World Cup milestones"),(1970,1998,2014,"FIFA World Cup milestones"),(2002,2010,2022,"FIFA World Cup milestones"),(1979,1996,2015,"Cricket World Cup milestones"),(1983,2007,2011,"India cricket title milestones"),(2008,2011,2019,"Modern cricket milestones")]:
        _add(theme, 3, "Sports History", f"For these {label}: {y1}, {y2}, {y3}. What is the total of the two consecutive year gaps?", _num_options((y3-y2)+(y2-y1), (4,8,12)), 0)


def _add_hard_mode_math():
    theme = "Hard Mode"
    for target,current,over_runs in [(200,146,11),(180,129,14),(250,191,9),(160,111,12),(220,164,7),(300,236,15),(175,122,8),(210,153,13),(190,141,10)]:
        _add(theme, 2, "Cricket", f"A chase target is {target}. The side is {current} and scores {over_runs} in the next over. How many runs are still needed?", _num_options(target-(current+over_runs)), 0)
    for w,d,gf,ga in [(3,1,10,6),(2,2,9,5),(4,0,12,7),(1,3,8,7),(3,2,14,9),(2,3,11,8),(5,0,16,10),(2,1,7,4),(4,2,15,11)]:
        _add(theme, 2, "Football", f"A team has {w} wins, {d} draws, {gf} goals for and {ga} against. Add its league points (3/1 system) to its goal difference. What is the result?", _num_options((3*w+d)+(gf-ga)), 0)
    hard = [
        ("Cricket",240-(168+12+9),"Target 240: a team is 168, then scores 12 in one over and 9 in the next. How many more runs are needed?"),
        ("Cricket",(72//8)+(48//6),"Add the required rate for 72 runs in 8 overs to the economy rate for 48 runs in 6 overs."),
        ("Football",(3*4+2)+(18-11),"A football team has 4 wins, 2 draws, 18 goals for and 11 against. Add its points to its goal difference."),
        ("Football",(3*3+3)+(14-8),"A football team has 3 wins, 3 draws, 14 goals for and 8 against. Add its points to its goal difference."),
        ("Basketball",6*2+4*3+7,"A player makes 6 two-pointers, 4 three-pointers and 7 free throws. What is the total?"),
        ("Cricket",300-(221+16),"Target 300: the team is 221 and then adds 16. How many more runs are required?"),
        ("Football",(3*5+1)+(20-12),"A football team has 5 wins, 1 draw, 20 goals for and 12 against. Add its points to its goal difference."),
        ("Basketball",8*2+3*3+5,"A player makes 8 two-pointers, 3 three-pointers and 5 free throws. What is the total?"),
        ("Cricket",(96//8)+(63//9),"Add the required rate for 96 runs in 8 overs to the economy rate for 63 runs in 9 overs."),
        ("Football",(3*2+4)+(13-9),"A football team has 2 wins, 4 draws, 13 goals for and 9 against. Add its points to its goal difference."),
        ("Cricket",275-(199+14+8),"Target 275: the team is 199, then scores 14 and 8 in the next two overs. How many more runs are needed?"),
        ("Basketball",10*2+2*3+4,"A player makes 10 two-pointers, 2 three-pointers and 4 free throws. What is the total?"),
    ]
    for sport,ans,text in hard:
        _add(theme, 3, sport, text, _num_options(ans), 0)


def _add_india_math():
    theme = "India Sports Special"
    for y1,y2,label in [(1983,2011,"India's men's ODI Cricket World Cup titles"),(2007,2024,"India's men's T20 World Cup titles"),(1996,2011,"Cricket World Cups hosted in the subcontinent with India as a co-host/host context"),(2008,2021,"Abhinav Bindra's Olympic gold year and Neeraj Chopra's Olympic gold year"),(2012,2016,"Olympic editions in which Indian badminton milestones were achieved"),(2000,2010,"Two decades of modern Indian sports growth"),(1980,2020,"Olympic editions linked to major Indian hockey medal history"),(2008,2016,"Olympic editions featuring landmark individual Indian medals"),(2011,2023,"ODI World Cup editions hosted in India")]:
        _add(theme, 2, "India Sports", f"For {label}, compare {y1} and {y2}. How many years apart are they?", _num_options(y2-y1, (4,8,12)), 0)
    for target,current in [(180,142),(250,207),(160,121),(220,173),(300,259),(175,136),(210,166),(190,149),(275,231)]:
        _add(theme, 2, "India Cricket", f"In an India cricket chase scenario, the target is {target} and the score is {current}. How many more runs are needed?", _num_options(target-current), 0)
    for a,b,c in [(1983,2007,2011),(2007,2011,2024),(2008,2016,2021),(1980,2008,2020),(1996,2011,2023),(2000,2012,2024)]:
        _add(theme,3,"India Sports",f"Consider Indian-sport milestone years {a}, {b} and {c}. What is the sum of the two consecutive year gaps?",_num_options((b-a)+(c-b),(4,8,12)),0)
    for target,current,r1,r2 in [(250,177,13,8),(300,218,14,11),(190,132,9,7),(220,151,12,10),(175,119,8,9),(275,196,15,12)]:
        _add(theme,3,"India Cricket",f"In an India chase scenario, target {target}: score {current}, then {r1} and {r2} runs are added in successive overs. How many more runs are needed?",_num_options(target-(current+r1+r2)),0)


def _add_championship_math():
    theme = "Championship Mix"
    for y1,y2,label in [(1930,1934,"successive early men's FIFA World Cups"),(1975,1979,"successive early men's Cricket World Cups"),(2008,2012,"four IPL seasons from launch to 2012"),(2010,2014,"men's FIFA World Cups"),(2011,2015,"men's Cricket World Cups"),(2014,2018,"men's FIFA World Cups"),(2015,2019,"men's Cricket World Cups"),(2018,2022,"men's FIFA World Cups"),(2019,2023,"men's Cricket World Cups")]:
        _add(theme,2,"Championships",f"The {label} referenced here are {y1} and {y2}. How many years apart are they?",_num_options(y2-y1,(2,4,6)),0)
    for w,d in [(2,1),(3,0),(1,3),(4,1),(2,2),(3,2),(5,0),(1,4),(4,2)]:
        _add(theme,2,"Tournament Football",f"In a championship group, a team gets {w} wins and {d} draws. At 3 points for a win and 1 for a draw, how many points is that?",_num_options(3*w+d),0)
    for teams in [8,16,32,4,64,128]:
        _add(theme,3,"Knockout Tournament",f"A single-elimination championship starts with {teams} teams and no byes. How many matches are needed to produce one champion?",_num_options(teams-1,(1,2,4)),0)
    for w,d,l in [(4,1,1),(3,2,1),(5,0,1),(2,3,1),(4,2,0),(3,3,0)]:
        games=w+d+l
        _add(theme,3,"Tournament Football",f"A team plays {games} group matches: {w} wins, {d} draws and {l} loss(es). At 3 points per win and 1 per draw, what is its total?",_num_options(3*w+d),0)


_add_cricket_math()
_add_football_math()
_add_mixed_math()
_add_legends_math()
_add_hard_mode_math()
_add_india_math()
_add_championship_math()


def theme_for_date(day):
    return THEMES[int(day.weekday())]


def _seed(day, theme):
    raw = f"{day.isoformat()}|{theme}|betroxy-daily-v2".encode()
    return int(hashlib.sha256(raw).hexdigest()[:16], 16)


def select_daily_questions(day, used_question_texts=None):
    """Return exactly 7 questions: 2 easy, 3 medium, 2 hard."""
    theme = theme_for_date(day)
    used = {str(x) for x in (used_question_texts or [])}
    rng = random.Random(_seed(day, theme))
    plan = ((1, 2), (2, 3), (3, 2))
    selected = []
    for difficulty, count in plan:
        primary = [q for q in QUESTION_BANK if q["theme"] == theme and q["difficulty"] == difficulty and q["question"] not in used]
        rng.shuffle(primary)
        chosen = primary[:count]
        if len(chosen) < count:
            fallback = [q for q in QUESTION_BANK if q["difficulty"] == difficulty and q["question"] not in used and q not in selected and q not in chosen]
            rng.shuffle(fallback)
            chosen += fallback[: count - len(chosen)]
        if len(chosen) < count:
            raise RuntimeError(f"Question bank exhausted for difficulty={difficulty}, theme={theme}")
        selected.extend(chosen)
    return theme, selected


def validate_bank():
    if not (150 <= len(QUESTION_BANK) <= 300):
        raise RuntimeError(f"Question bank size out of range: {len(QUESTION_BANK)}")
    seen = set()
    counts = {}
    for item in QUESTION_BANK:
        text = item["question"]
        if text in seen:
            raise RuntimeError(f"Duplicate question text: {text}")
        seen.add(text)
        if len(item["options"]) != 4 or len(set(item["options"])) != 4:
            raise RuntimeError(f"Invalid options: {text}")
        if item["correct"] not in (0, 1, 2, 3):
            raise RuntimeError(f"Invalid correct index: {text}")
        key = (item["theme"], item["difficulty"])
        counts[key] = counts.get(key, 0) + 1
    for theme in THEMES.values():
        expected = {1: 10, 2: 18, 3: 12}
        actual = {d: counts.get((theme, d), 0) for d in (1, 2, 3)}
        if actual != expected:
            raise RuntimeError(f"Unexpected bank distribution for {theme}: {actual}")
    return True


validate_bank()
