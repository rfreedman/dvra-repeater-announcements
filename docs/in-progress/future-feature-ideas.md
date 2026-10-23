# Future Feature Ideas (last update 2026-10-23)

## Access To UI
* already accessible on the local network
* already has passwords, roles, and user management
* UI is already available on the local network (with http and a port number)
* Investigate using TailScale to provide remote access with https
* With remote access, provide ssh for admin


## Make config accessible and updatable from the Admin UI
 * How to either re-init from saved config (preferable) or auto-restart the app
 * Consider retaining some state, such as repeater "quiescent time"

## Play Amateur Radio Newsline Weekly Broadcast
* ARNL releases a "Report" containing several stories once per week, on Thursday or Friday, for release on Friday

* An existing project live in arnewsline-audio-splitter that:
  * checks for, and if available and not already downloaded, downloads an approx. 15-20 minute mp3 file with all of the 'stories' and it's accompanying script .txt file
  * splits the large mp3 file into smaller, sequentially numbered mp3 files, each less than the 3-minute repeater timeout
  * adds a 'station-id.mp3' file at the location in the sequence that corresponds to a "break/station id" that is an unspoken entry in the script,
    and which occurs as late as possible prior to the 10-minute mark to accommodate the FCC-required once-per-ten-minute station ID requirement.

* Either integrate that project's code into this one, or keep it separate?

* Schedule a cron job to run the download/process script starting sometime on Thursday every week
  * can / should we arrange to stop checking for the download once it is successfully downloaded for the current week?

* Add support to this (the announcement program) for playing the processed ARNL mp3 files as a new type of announcement
  * **Drop PTT** for 2 seconds between playing each of the mp3 files
  * **Substitute TTS "station id"** announcement for the zero-byte station-id.mp3 marker file 
    * allow user to select an existing announcement, enter one in the new announcement, or both?
  * Consider adding support for TTS (either existing or entered in the news announcement) both before and after playing the news.
  * should be scheduled once weekly, probably on Friday, at a minimum
    * could schedule additional days/timess

  * implement as a new schedule type, or make the news an entirely separate thing 
    * If a separate thing, still need its schedule to be coordinated with the announcement schedules to avoid conflict.
      * give the news a very high priority so that it preempts all other announcements (except emergency)

## Pre-recorded audio in announcements
* Once the ARNL news feed has been implemented (adding support for playing mp3 files) consider allowing the use of pre-recorded 
  audio files (mp3, wav, etc.) for 'normal' announcements, either alone, or mixed with TTS.
  * Implement as an embedded tag, similar to the current \[pause\] tag?
