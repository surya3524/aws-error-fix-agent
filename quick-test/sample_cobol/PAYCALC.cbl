       IDENTIFICATION DIVISION.
       PROGRAM-ID. PAYCALC.
      *----------------------------------------------------------------*
      * PAYCALC - WEEKLY GROSS PAY FROM TIMESHEET FILE                 *
      *----------------------------------------------------------------*
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT TIMESHEET ASSIGN TO TIMEIN.
           SELECT PAYOUT    ASSIGN TO PAYOUT.
       DATA DIVISION.
       FILE SECTION.
       FD  TIMESHEET RECORDING MODE F.
       COPY EMPREC.
       FD  PAYOUT RECORDING MODE F.
       01  PAY-LINE                PIC X(80).
       WORKING-STORAGE SECTION.
       01  WS-EOF                  PIC X VALUE 'N'.
       01  WS-GROSS                PIC 9(7)V99 VALUE ZERO.
       01  WS-TOTAL-HOURS          PIC 9(7) VALUE ZERO.
       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT TIMESHEET OUTPUT PAYOUT
           PERFORM UNTIL WS-EOF = 'Y'
               READ TIMESHEET
                   AT END MOVE 'Y' TO WS-EOF
                   NOT AT END PERFORM CALC-PAY
               END-READ
           END-PERFORM
           CLOSE TIMESHEET PAYOUT
           STOP RUN.
       CALC-PAY.
           ADD EMP-HOURS TO WS-TOTAL-HOURS
           COMPUTE WS-GROSS = EMP-HOURS * EMP-RATE
           MOVE SPACES TO PAY-LINE
           STRING EMP-ID ' ' WS-GROSS DELIMITED BY SIZE INTO PAY-LINE
           WRITE PAY-LINE.
