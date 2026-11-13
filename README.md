# DVRA Repeater Announcements (DVRA_RA)

[![Test](https://github.com/rfreedman/dvra-repeater-announcements/actions/workflows/test.yml/badge.svg)](https://github.com/rfreedman/dvra-repeater-announcements/actions/workflows/test.yml)

Software for scheduled Amateur Radio repeater announcements. 

## About

This project implements an announcement controller for Amateur Radio repeaters.

There are several hardware-based repeater controllers that include announcement capabilities,
but most either work with hard-coded phrases that you must string together, or have           very basic 
and robotic-sounding text-to-speech (TTS).

Also, a built-in repeater controller typically (always?) controls a single repeater.

At the Delaware Valley Radio Association (station callsign W2ZQ), we have two
repeaters - one for VHF, and one for UHF, and they are in different locations.

Our existing system announces on both repeaters at the same time by transmitting
on two radios - one on each frequency. The repeaters receive these transmissions
and repeat them the same way that they do for any normal transmission.

We wanted to continue to make the same announcements on both repeaters, at the same time,
but we wanted a system that provided better TTS voices and more flexible and accessible 
scheduling than our existing announcement system.

This is the result, and what will be put online soon.

## Features
 * **TTS** - better-sounding text-to-speech by using the Piper TTS library

 * **Accessibility** - admin interface is web-based, so that it can be used
   anywhere and on any computer that can reach the server.<br/>
   (We can use the admin interface on our shack's LAN, or from home via a VPN - it is NOT
   exposed to the open internet).

 * **Flexible Scheduling** - instead of requiring announcements to be scheduled at
   specific times, DVRA_RA divides each 24-hour period into 48 time slots, one for 
   each hour and half-hour. Announcements can then be scheduled for multiple slots
   with a single 'schedule'. <br/><br/>Repeating schedules are modeled after common calendar repeating event 
   schedules, with options like certain times on certain days of the week, or on 
   a particular day of the week on a specific week of the year, for example, 7:00pm
   on the third Wednesday of the month. <br/><br/> See scheduling for more info

 * **Amateur Radio Newsline**
   Many repeater controllers have the ability to transmit the Amateur Radio Newsline via EchoLink,
   or by manually copying the ARNewsline mp3 file. To enable transmitting the Newsline on non-EchoLink repeaters, and without manual intervention, DVRA_RA can be configured
   to automatically download the Newsline mp3 each week.<br/><br/>
   To accommodate repeater controllers that cannot be programmed to automatically disable the repeater
   timeout while the newsline is being transmitted, DVRA_RA automatically breaks up the NewsLine into  
   multiple pieces that fit within the repeater timeout window.
  
