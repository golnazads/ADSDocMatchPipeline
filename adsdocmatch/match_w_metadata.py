import os
from adsputils import setup_logging, load_config
from adsdocmatch.pub_parser import get_pub_metadata
from adsdocmatch.oracle_util import OracleUtil
from pyingest.parsers.arxiv import ArxivParser
import time
import re
import csv

proj_home = os.path.realpath(os.path.join(os.path.dirname(__file__), "../"))
conf = load_config(proj_home=proj_home)

logger = setup_logging("docmatching", level=conf.get("LOGGING_LEVEL", "WARN"), proj_home=proj_home, attach_stdout=conf.get("LOG_STDOUT", "FALSE"))

class MatchMetadata():

    MUST_MATCH = ['Astrophysics', 'Physics']
    DOCTYPE_THESIS = ['phdthesis', 'mastersthesis']

    re_admin_notes = re.compile(r'arXiv admin note: (.*)$')
    re_doi = re.compile(r'doi:\s*(10\.\d{4,9}/\S+\w)', re.IGNORECASE)
    re_doctype_thesis = re.compile(r'\b(thesis)\b', re.IGNORECASE)
    re_doctype_errata = re.compile(r'(^errat(a|um))\b', re.IGNORECASE)
    re_doctype_bookreview = re.compile(r'\bbook[s|\s|\-]*review[s|ed]*', re.IGNORECASE)

    ARXIV_PARSER = ArxivParser()
    ORACLE_UTIL = OracleUtil()

    def get_input_filenames(self, filename):
        """
        read input file and return list of arXiv metadata full filenames

        :param filename:
        :return:
        """
        filenames = []
        try:
            with open(filename, 'r') as fp:
                for filename in fp.readlines():
                    filenames.append(filename.rstrip('\r\n'))
        except Exception as e:
            logger.error('Unable to open/read input file', e)
        return filenames

    def process_results(self, results, separator):
        """

        :param results:
        :param separator:
        :return:
        """
        matches = []
        for result in results:
            if result.get('matched_bibcode', None):
                matches.append(separator.join([str(result.get(field, '')) for field in ['source_bibcode', 'matched_bibcode', 'label', 'confidence', 'score', 'comment']]))
        if matches:
            return matches
        # when error, return status_code
        return ['%s %s status_code=%s' % (results[0].get('source_bibcode', ''), results[0].get('comment', ''), results[0].get('status_code', ''))]

    def write_results(self, result_filename, matches):
        """

        :param result_filename:
        :param matches:
        :return:
        """
        csv_file = result_filename
        if os.path.exists(csv_file):
            fp = open(csv_file, 'a')
        else:
            fp = open(csv_file, 'w')
            # new file, write header line
            fp.write('source bibcode (link),verified bibcode,matched bibcode (link),label,confidence,matched scores,comment\n')

        hyperlink_format = '"=HYPERLINK(""https://ui.adsabs.harvard.edu/abs/%s/abstract"",""%s"")"'
        double_quote = '"%s"'

        for match in matches:
            match_parts = match.split('\t')
            if len(match_parts) == 6:
                source_bibcode = match_parts[0]
                matched_bibcode = match_parts[1]
                fp.write('%s,,%s,%s,%s,%s,%s\n' % (
                    hyperlink_format % (source_bibcode, source_bibcode),
                    hyperlink_format % (matched_bibcode, matched_bibcode),
                    match_parts[2],
                    match_parts[3],
                    double_quote % match_parts[4],
                    double_quote % match_parts[5],
                ))
            else:
                # it is an error write it out
                fp.write("%s\n" % match_parts)
        fp.close()

    def match_to_arXiv(self, filename):
        """
        read and parse arXiv metadata file
        return list of bibcodes and scores for the matches in decreasing order
    
        :param filename:
        :return:
        """
        try:
            with open(filename, 'rb') as pub_fp:
                return self.ORACLE_UTIL.get_matches(get_pub_metadata(pub_fp.read()), 'article')
        except Exception as e:
            logger.error('Exception: %s'%e)
            return
    
    def single_match_to_arXiv(self, pub_filename):
        """
        when user submits a single pub metadata file for matching
    
        :param pub_filename:
        :return:
        """
        results = self.match_to_arXiv(pub_filename)
        if results:
            return self.process_results(results, '\t')
        return None
    
    def batch_match_to_arXiv(self, input_filename, result_filename):
        """
    
        :param input_filename: contains list of filenames
        :param result_filename: name of result file to write to
        :return:
        """
        filenames = self.get_input_filenames(input_filename)
        if len(filenames) > 0:
            if result_filename:
                # one file at a time, parse and score, and then write the result to the file
                for pub_filename in filenames:
                    matches = self.single_match_to_arXiv(pub_filename)
                    self.write_results(result_filename, matches)
                    # wait a second before the next attempt
                    time.sleep(1)

    def match_to_pub(self, filename):
        """
        read and parse arXiv metadata file
        return list of bibcodes and scores for the matches in decreasing order
    
        :param filename:
        :return:
        """
        try:
            with open(filename, 'rb') as arxiv_fp:
                metadata = self.ARXIV_PARSER.parse(arxiv_fp)
                comments = ' '.join(metadata.get('comments', []))
                # extract doi out of comments if there are any
                match = self.re_doi.search(comments)
                if match:
                    metadata['doi'] = match.group(1)
                else:
                    doi = metadata.get('properties', {}).get('DOI', None)
                    if doi:
                        metadata['doi'] = doi.replace('doi:', '')
                match_doctype = None
                title = metadata.get('title')
                # check title for erratum
                match = self.re_doctype_errata.search(title)
                if match:
                    match_doctype = ['erratum']
                else:
                    match = self.re_doctype_bookreview.search(title)
                    if match:
                        match_doctype = ['bookreview']
                    else:
                        # check both comments and title for thesis
                        match = self.re_doctype_thesis.search("%s %s"%(comments, title))
                        if match:
                            match_doctype = ['phdthesis', 'mastersthesis']
                mustmatch = any(category in metadata.get('keywords', '') for category in self.MUST_MATCH)
                return self.add_metadata_comment(self.ORACLE_UTIL.get_matches(metadata, 'eprint', mustmatch, match_doctype), comments)
        except Exception as e:
            logger.error('Exception: %s'%e)
            return
    
    def single_match_to_pub(self, arXiv_filename):
        """
        when user submits a single arxiv metadata file for matching
    
        :param arxiv_filename:
        :return:
        """
        results = self.match_to_pub(arXiv_filename)
        if results:
            return self.process_results(results, '\t')
        return None
    
    def batch_match_to_pub(self, input_filename, result_filename):
        """
    
        :param input_filename: contains list of filenames
        :param result_filename: name of result file to write to
        :return:
        """
        filenames = self.get_input_filenames(input_filename)
        if len(filenames) > 0:
            if result_filename:
                # one file at a time, parse and score, and then write the result to the file
                for arXiv_filename in filenames:
                    matches = self.single_match_to_pub(arXiv_filename)
                    self.write_results(result_filename, matches)
                    # wait a second before the next attempt
                    time.sleep(1)

    def add_metadata_comment(self, results, comments):
        """
    
        :param results:
        :param comments:
        :return:
        """
        match = self.re_admin_notes.search(comments)
        if match:
            admin_notes = match.group(1)
            for result in results:
                result['comment'] = ('%s %s'%(result.get('comment', ''), admin_notes)).strip()
        return results

    def read_classic_results(self, classic, source):
        """
        
        :param classic: 
        :param source: 
        :return: 
        """
        results = {}
        with open(classic, 'r') as fp:
            for line in fp.readlines():
                if len(line) > 1:
                    columns = line[:-1].split('\t')
                    if source == 'eprint':
                        results[columns[0]] = columns[1]
                    elif source == 'pub':
                        results[columns[1]] = columns[0]
        return results
    
    def read_docmatch_results(self, filename):
        """
    
        :param filename:
        :return:
        """
        results = []
        with open(filename, 'r') as fp:
            reader = csv.reader(fp, delimiter=',')
            next(reader)
            for columns in reader:
                results.append(columns)
        return results

    def combine_classic_docmatch_results(self, classic_results, docmatch_results):
        """
    
        :param classic_results:
        :param docmatch_results:
        :return:
        """
        combined_results = []
        combined_results.append(['source bibcode (link)','classic bibcode (link)','curator comment','verified bibcode','matched bibcode (link)','comment','label','confidence','matched scores'])
    
        hyperlink_format = '"=HYPERLINK(""https://ui.adsabs.harvard.edu/abs/%s/abstract"",""%s"")"'
        for nowadays_result in docmatch_results:
            # if there was an error in the csv file, transfer it and move on
            if len(nowadays_result) == 1:
                combined_results.append(nowadays_result)
                continue
            try:
                # insert two columns: 'classic bibcode (link)','curator comment' between the source and matched bibcode columns
                classic_bibcode = classic_results.get(nowadays_result[0][-21:-2], '')
                classic_bibcode_link = hyperlink_format % (classic_bibcode, classic_bibcode) if classic_bibcode else ''
                # need to format the two linked columns again
                source_bibcode_link = '"%s"'%nowadays_result[0].replace('"','""')
                matched_bibcode_link = '"%s"'%nowadays_result[2].replace('"','""') if not nowadays_result[2][-21:-2].startswith('.') else ''
                combined_results.append([source_bibcode_link, classic_bibcode_link, '', '', matched_bibcode_link, '"%s"'%nowadays_result[6], nowadays_result[3], nowadays_result[4], '"%s"'%nowadays_result[5]])
            except:
                combined_results.append(nowadays_result)
        return combined_results
    
    def write_combined_results(self, combined_results, output_filename):
        """
    
        :param combined_results:
        :param output_filename:
        :return:
        """
        with open(output_filename, 'w') as fp:
            fp.write(','.join(combined_results[0]) + '\n')
            # error lines are one element that have no confidence column
            combined_results = sorted(combined_results[1:], key=lambda result: float(result[7]) if len(result) > 7 else -1)
            for combined_result in combined_results:
                # error lines are one element, include them
                if len(combined_result) == 1:
                    fp.write(','.join(combined_result) + '\n')
                # include only the lines with classic bibcode, or matched bibcode
                elif len(combined_result[1]) > 0 or len(combined_result[4]) > 0:
                    # if there is a classic match see if it agrees or disagrees with oracle
                    try:
                        if len(combined_result[1]) > 0:
                            combined_result[2] = 'agree' if combined_result[1] == combined_result[4] else 'disagree'
                        # if there is a multi match and confidence is high
                        # or if there was no abstract for comparison and confidence is high
                        # mark it to be verified
                        elif (len(combined_result) >= 8 and float(combined_result[7]) >= 0.5 and
                                  (('None' in combined_result[8]) or ('Multi match' in combined_result[5]))):
                            combined_result[2] = 'verify'
                    except Exception as err:
                        logger.warning("Error combining classic and docmatcher results: %s" % err)
                    fp.write(','.join(combined_result)+'\n')

    def merge_classic_docmatch_results(self, classic_filename, docmatch_filename, output_filename):
        """

        :param classic_filename:
        :param docmatch_filename:
        :param output_filename:
        :return:
        """
        if docmatch_filename.endswith(conf.get('DOCMATCHPIPELINE_EPRINT_RESULT_FILENAME', 'default')):
            source = 'eprint'
        elif docmatch_filename.endswith(conf.get('DOCMATCHPIPELINE_PUB_RESULT_FILENAME', 'default')):
            source = 'pub'
        else:
            logger.error('Unable to determine type of result file, no combined file created.')
            return

        classic_results = self.read_classic_results(classic_filename, source)
        docmatch_results = self.read_docmatch_results(docmatch_filename)
        if classic_results and docmatch_results:
            combined_results = self.combine_classic_docmatch_results(classic_results, docmatch_results)
            if combined_results:
                self.write_combined_results(combined_results, output_filename)

    def process_match_to_arXiv(self, path):
        """

        :param path:
        :return:
        """
        input_filename = "%s%s" % (path, conf.get('DOCMATCHPIPELINE_INPUT_FILENAME', 'default'))
        result_filename = "%s%s" % (path, conf.get('DOCMATCHPIPELINE_PUB_RESULT_FILENAME', 'default'))

        self.batch_match_to_arXiv(input_filename, result_filename)

        classic_matched_filename = "%s%s" % (path, conf.get('DOCMATCHPIPELINE_CLASSIC_MATCHES_FILENAME', 'default'))
        combined_output_filename = "%s%s" % (path, conf.get('DOCMATCHPIPELINE_PUB_COMBINED_FILENAME', 'default'))

        if os.path.exists(classic_matched_filename):
            self.merge_classic_docmatch_results(classic_matched_filename, result_filename, combined_output_filename)
            return combined_output_filename
        else:
            return result_filename

    def process_match_to_pub(self, path):
        """

        :param path:
        :return:
        """
        input_filename = "%s%s" % (path, conf.get('DOCMATCHPIPELINE_INPUT_FILENAME', 'default'))
        result_filename = "%s%s" % (path, conf.get('DOCMATCHPIPELINE_EPRINT_RESULT_FILENAME', 'default'))

        self.batch_match_to_pub(input_filename, result_filename)

        classic_matched_filename = "%s%s" % (path, conf.get('DOCMATCHPIPELINE_CLASSIC_MATCHES_FILENAME', 'default'))
        combined_output_filename = "%s%s" % (path, conf.get('DOCMATCHPIPELINE_EPRINT_COMBINED_FILENAME', 'default'))

        if os.path.exists(classic_matched_filename):
            self.merge_classic_docmatch_results(classic_matched_filename, result_filename, combined_output_filename)
            return combined_output_filename
        else:
            return result_filename