      *----------------------------------------------------------------*
      * EMPREC - EMPLOYEE TIMESHEET RECORD (80 BYTES)                  *
      *----------------------------------------------------------------*
       01  EMP-RECORD.
           05  EMP-ID              PIC X(6).
           05  EMP-NAME            PIC X(30).
           05  EMP-HOURS           PIC 9(3).
           05  EMP-RATE            PIC 9(3)V99.
           05  FILLER              PIC X(36).
